"""Command-line entry point (``clipapp-engine``)."""

import json
import wave
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import Progress
from rich.table import Table

from clipapp_engine.doctor import run_doctor
from clipapp_engine.media.audio import extract_track_wav
from clipapp_engine.media.errors import MediaError
from clipapp_engine.media.probe import probe
from clipapp_engine.silence.detect import detect_silences, wav_duration_s
from clipapp_engine.silence.noise import suggest_threshold
from clipapp_engine.silence.ranges import compute_keep_ranges, stats
from clipapp_engine.silence.settings import SilenceSettings

app = typer.Typer(help="ClipApp local engine.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """ClipApp local engine."""


@app.command()
def doctor(
    as_json: bool = typer.Option(False, "--json", help="Print the report as JSON."),
) -> None:
    """Report FFmpeg versions, usable encoders, GPUs, CUDA status and free disk space."""
    report = run_doctor()
    if as_json:
        typer.echo(json.dumps(report.to_dict(), indent=2))
        return

    table = Table(title="ClipApp doctor", show_header=True)
    table.add_column("Check")
    table.add_column("Result")
    table.add_row("ffmpeg", report.ffmpeg_version)
    table.add_row("ffprobe", report.ffprobe_version)
    table.add_row("GPU", "\n".join(report.gpus) or "none detected")
    for enc in report.encoders:
        status = "[green]usable[/]" if enc.usable else f"[red]no[/] ({enc.error})"
        table.add_row(f"encoder {enc.name}", status)
    table.add_row("CUDA (ctranslate2)", report.cuda)
    table.add_row("data dir", report.data_dir)
    table.add_row("disk free", f"{report.disk_free_bytes / 1024**3:.1f} GiB")
    Console().print(table)


@app.command("probe")
def probe_cmd(
    file: Annotated[Path, typer.Argument(help="Video file to inspect.")],
    as_json: bool = typer.Option(False, "--json", help="Print the result as JSON."),
) -> None:
    """Show duration, video format and audio tracks of a media file."""
    try:
        info = probe(file)
    except MediaError as exc:
        Console(stderr=True).print(f"[red]error:[/] {escape(str(exc))}")
        raise typer.Exit(1) from exc
    if as_json:
        typer.echo(info.model_dump_json(indent=2))
        return

    console = Console()
    console.print(f"[bold]{escape(str(info.path))}[/]")
    vfr = " (VFR)" if info.is_vfr else ""
    console.print(
        f"{info.duration_s:.2f} s · {info.width}x{info.height} · {float(info.fps):.3f} fps "
        f"({info.fps}){vfr} · {info.video_codec} {info.pix_fmt or ''} · rotation {info.rotation}"
    )
    table = Table(title="Audio tracks")
    for col in ("#", "stream", "codec", "ch", "Hz", "lang", "title"):
        table.add_column(col)
    for t in info.audio_tracks:
        table.add_row(
            str(t.index),
            str(t.stream_index),
            t.codec,
            str(t.channels),
            str(t.sample_rate),
            t.language or "",
            escape(t.title or ""),
        )
    console.print(table)


_DEFAULTS = SilenceSettings()


@app.command("silence")
def silence_cmd(  # noqa: PLR0913, PLR0917
    file: Annotated[Path, typer.Argument(help="Recording to analyze.")],
    track: int = typer.Option(1, "--track", help="0-based audio track (OBS mic track = 1)."),
    threshold: float | None = typer.Option(None, "--threshold", help="Silence level in dBFS."),
    min_silence: float = typer.Option(_DEFAULTS.min_silence_s, "--min-silence"),
    padding: float = typer.Option(_DEFAULTS.padding_s, "--padding"),
    min_keep: float = typer.Option(_DEFAULTS.min_keep_s, "--min-keep"),
    suggest: bool = typer.Option(
        False, "--suggest", help="Suggest a threshold (used unless --threshold is given)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print silences and keep ranges as JSON."),
) -> None:
    """Detect silences on a track and report the ranges to keep."""
    err = Console(stderr=True)
    try:
        with Progress(console=err, transient=True) as bar:
            task = bar.add_task("Extracting audio", total=1.0)
            wav = extract_track_wav(file, track, lambda p: bar.update(task, completed=p))
            suggestion = suggest_threshold(wav) if suggest else None
            if threshold is None:
                threshold = suggestion.threshold_db if suggestion else _DEFAULTS.threshold_db
            settings = SilenceSettings(
                threshold_db=threshold,
                min_silence_s=min_silence,
                padding_s=padding,
                min_keep_s=min_keep,
            )
            bar.update(task, description="Detecting silences", completed=0)
            silences = detect_silences(
                wav,
                settings.threshold_db,
                settings.min_silence_s,
                lambda p: bar.update(task, completed=p),
            )
    except (MediaError, OSError, ValueError, wave.Error) as exc:
        err.print(f"[red]error:[/] {escape(str(exc))}")
        raise typer.Exit(1) from exc

    duration = wav_duration_s(wav)
    keep = compute_keep_ranges(duration, silences, settings.padding_s, settings.min_keep_s)
    summary = stats(duration, keep)
    if as_json:
        out = {
            "settings": settings.model_dump(),
            "suggestion": suggestion.model_dump() if suggestion else None,
            "stats": summary.model_dump(),
            "silences": silences,
            "keep_ranges": keep,
        }
        typer.echo(json.dumps(out, indent=2))
        return

    console = Console()
    if suggestion:
        console.print(
            f"Suggested threshold {suggestion.threshold_db} dB "
            f"(floor {suggestion.floor_db} dB, voice {suggestion.voice_db} dB)"
        )
        if suggestion.warning:
            console.print(f"[yellow]warning:[/] {suggestion.warning}")
    table = Table(title=f"Silences (threshold {settings.threshold_db} dB)")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("original", f"{summary.original_s:.2f} s")
    table.add_row("final", f"{summary.final_s:.2f} s")
    table.add_row("removed", f"{summary.removed_pct:.1f} %")
    table.add_row("cuts", str(summary.cuts))
    console.print(table)
