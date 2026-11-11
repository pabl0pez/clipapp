"""Command-line entry point (``clipapp-engine``)."""

import json
import logging
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import Progress
from rich.table import Table

from clipapp_engine.config import data_dir
from clipapp_engine.doctor import run_doctor
from clipapp_engine.export.full import RenderOptions, render_without_silences
from clipapp_engine.media.audio import extract_track_wav
from clipapp_engine.media.encoders import EncoderError, save_encoder_cache
from clipapp_engine.media.errors import MediaError
from clipapp_engine.media.probe import probe
from clipapp_engine.silence.detect import detect_silences, wav_duration_s
from clipapp_engine.silence.noise import ThresholdSuggestion, suggest_threshold
from clipapp_engine.silence.ranges import Range, SilenceStats, compute_keep_ranges, stats
from clipapp_engine.silence.settings import SilenceSettings

app = typer.Typer(help="ClipApp local engine.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """ClipApp local engine."""
    logging.basicConfig(
        filename=data_dir() / "logs" / "engine.log",
        encoding="utf-8",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@app.command()
def doctor(
    as_json: bool = typer.Option(False, "--json", help="Print the report as JSON."),
) -> None:
    """Report FFmpeg versions, usable encoders, GPUs, CUDA status and free disk space."""
    report = run_doctor()
    if report.ffmpeg_version != "not found":
        save_encoder_cache(report.ffmpeg_version, report.encoders)
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
_ANALYSIS_ERRORS = (MediaError, OSError, ValueError, wave.Error)


@dataclass(frozen=True)
class _Analysis:
    """Result of the silence pipeline shared by ``silence`` and ``render``."""

    settings: SilenceSettings
    suggestion: ThresholdSuggestion | None
    silences: list[Range]
    keep: list[Range]
    summary: SilenceStats


def _analyze(  # noqa: PLR0913, PLR0917
    file: Path,
    track: int,
    threshold: float | None,
    min_silence: float,
    padding: float,
    min_keep: float,
    suggest: bool,
    bar: Progress,
) -> _Analysis:
    """Extract the track WAV, detect silences and compute the keep ranges."""
    task = bar.add_task("Extracting audio", total=1.0)
    wav = extract_track_wav(file, track, lambda p: bar.update(task, completed=p))
    suggestion = suggest_threshold(wav) if suggest else None
    if threshold is None:
        threshold = suggestion.threshold_db if suggestion else _DEFAULTS.threshold_db
    settings = SilenceSettings(
        threshold_db=threshold, min_silence_s=min_silence, padding_s=padding, min_keep_s=min_keep
    )
    bar.update(task, description="Detecting silences", completed=0)
    silences = detect_silences(
        wav, settings.threshold_db, settings.min_silence_s, lambda p: bar.update(task, completed=p)
    )
    bar.remove_task(task)
    duration = wav_duration_s(wav)
    keep = compute_keep_ranges(duration, silences, settings.padding_s, settings.min_keep_s)
    return _Analysis(settings, suggestion, silences, keep, stats(duration, keep))


def _print_analysis(console: Console, result: _Analysis) -> None:
    """Print the threshold suggestion (if any) and the cut statistics table."""
    suggestion, summary = result.suggestion, result.summary
    if suggestion:
        console.print(
            f"Suggested threshold {suggestion.threshold_db} dB "
            f"(floor {suggestion.floor_db} dB, voice {suggestion.voice_db} dB)"
        )
        if suggestion.warning:
            console.print(f"[yellow]warning:[/] {suggestion.warning}")
    table = Table(title=f"Silences (threshold {result.settings.threshold_db} dB)")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("original", f"{summary.original_s:.2f} s")
    table.add_row("final", f"{summary.final_s:.2f} s")
    table.add_row("removed", f"{summary.removed_pct:.1f} %")
    table.add_row("cuts", str(summary.cuts))
    console.print(table)


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
            result = _analyze(file, track, threshold, min_silence, padding, min_keep, suggest, bar)
    except _ANALYSIS_ERRORS as exc:
        err.print(f"[red]error:[/] {escape(str(exc))}")
        raise typer.Exit(1) from exc

    if as_json:
        out = {
            "settings": result.settings.model_dump(),
            "suggestion": result.suggestion.model_dump() if result.suggestion else None,
            "stats": result.summary.model_dump(),
            "silences": result.silences,
            "keep_ranges": result.keep,
        }
        typer.echo(json.dumps(out, indent=2))
        return
    _print_analysis(Console(), result)


_RENDER = RenderOptions()


@app.command("render")
def render_cmd(  # noqa: PLR0913, PLR0917
    file: Annotated[Path, typer.Argument(help="Recording to render.")],
    output: Annotated[Path, typer.Option("-o", "--output", help="Output .mp4 file.")],
    track_mic: int = typer.Option(_RENDER.mic_track, "--track-mic", help="0-based mic track."),
    track_game: int = typer.Option(_RENDER.game_track, "--track-game", help="0-based game track."),
    mic_volume: float = typer.Option(_RENDER.mic_volume, "--mic-volume"),
    game_volume: float = typer.Option(_RENDER.game_volume, "--game-volume"),
    encoder: str | None = typer.Option(None, "--encoder", help="Force an FFmpeg encoder."),
    threshold: float | None = typer.Option(None, "--threshold", help="Silence level in dBFS."),
    min_silence: float = typer.Option(_DEFAULTS.min_silence_s, "--min-silence"),
    padding: float = typer.Option(_DEFAULTS.padding_s, "--padding"),
    min_keep: float = typer.Option(_DEFAULTS.min_keep_s, "--min-keep"),
    suggest: bool = typer.Option(
        False, "--suggest", help="Suggest a threshold (used unless --threshold is given)."
    ),
) -> None:
    """Remove silences (detected on the mic track) and export the result as MP4."""
    err = Console(stderr=True)
    options = RenderOptions(
        mic_track=track_mic,
        game_track=track_game,
        mic_volume=mic_volume,
        game_volume=game_volume,
        encoder=encoder,
    )
    try:
        with Progress(console=err, transient=True) as bar:
            result = _analyze(
                file, track_mic, threshold, min_silence, padding, min_keep, suggest, bar
            )
            task = bar.add_task("Rendering", total=1.0)
            used = render_without_silences(
                file, result.keep, options, output, lambda p: bar.update(task, completed=p)
            )
    except (*_ANALYSIS_ERRORS, EncoderError) as exc:
        err.print(f"[red]error:[/] {escape(str(exc))}")
        raise typer.Exit(1) from exc

    console = Console()
    _print_analysis(console, result)
    console.print(f"encoder: [bold]{used}[/] -> {escape(str(output))}")
