"""Command-line entry point (``clipapp-engine``)."""

import json
import logging
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from rich.progress import Progress
from rich.table import Table

from clipapp_engine.config import data_dir
from clipapp_engine.doctor import run_doctor
from clipapp_engine.export.clip import ClipOptions
from clipapp_engine.export.clips_file import ClipsFileError, ClipSpec, parse_clips_file, parse_time
from clipapp_engine.export.full import RenderOptions, render_without_silences
from clipapp_engine.export.pipeline import export_clip
from clipapp_engine.media.audio import extract_track_wav
from clipapp_engine.media.encoders import EncoderError, save_encoder_cache
from clipapp_engine.media.errors import MediaError
from clipapp_engine.media.prepare import Manifest, prepare
from clipapp_engine.media.probe import probe
from clipapp_engine.silence.detect import detect_silences, wav_duration_s
from clipapp_engine.silence.noise import ThresholdSuggestion, suggest_threshold
from clipapp_engine.silence.ranges import Range, SilenceStats, compute_keep_ranges, stats
from clipapp_engine.silence.settings import SilenceSettings
from clipapp_engine.subtitles.ass import build_ass
from clipapp_engine.subtitles.srt import build_srt
from clipapp_engine.subtitles.style import TEMPLATES
from clipapp_engine.transcribe.device import pick_device
from clipapp_engine.transcribe.download import ensure_model
from clipapp_engine.transcribe.glossary import Glossary
from clipapp_engine.transcribe.transcript import Transcript
from clipapp_engine.transcribe.whisper import transcribe_range

app = typer.Typer(help="ClipApp local engine.", no_args_is_help=True)
models_app = typer.Typer(help="Manage local Whisper models.", no_args_is_help=True)
app.add_typer(models_app, name="models")


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


@app.command("prepare")
def prepare_cmd(
    file: Annotated[Path, typer.Argument(help="Recording to prepare for the UI.")],
    track_mic: int = typer.Option(1, "--track-mic", help="0-based mic track (OBS mic track = 1)."),
    force: bool = typer.Option(False, "--force", help="Rebuild every cached file."),
) -> None:
    """Build the proxy, waveform and thumbnails the UI uses, then print their sizes."""
    err = Console(stderr=True)
    try:
        with Progress(console=err, transient=True) as bar:
            task = bar.add_task("Preparing", total=1.0)
            manifest_file = prepare(
                file,
                track_mic,
                lambda step, p: bar.update(task, description=step, completed=p),
                force,
            )
    except (*_ANALYSIS_ERRORS, EncoderError) as exc:
        err.print(f"[red]error:[/] {escape(str(exc))}")
        raise typer.Exit(1) from exc

    manifest = Manifest.model_validate_json(manifest_file.read_text(encoding="utf-8"))
    table = Table(title=escape(str(manifest_file.parent)))
    table.add_column("File")
    table.add_column("Size", justify="right")
    for name in (manifest.wav, manifest.waveform, manifest.proxy, manifest.sprite):
        size = (manifest_file.parent / name).stat().st_size
        table.add_row(name, f"{size / 1024**2:.2f} MiB")
    Console().print(table)


@models_app.command("download")
def models_download(
    name: Annotated[str, typer.Argument(help="Model name, e.g. small or large-v3-turbo.")],
    force: bool = typer.Option(False, "--force", help="Download again even if present."),
) -> None:
    """Download a Whisper model into the data folder (needed once; then works offline)."""
    try:
        folder = ensure_model(name, force)
    except Exception as exc:  # network/HF errors have no common base class
        Console(stderr=True).print(f"[red]error:[/] {escape(str(exc))}")
        raise typer.Exit(1) from exc
    typer.echo(str(folder))


@app.command("transcribe")
def transcribe_cmd(  # noqa: PLR0913, PLR0917
    file: Annotated[Path, typer.Argument(help="Recording to transcribe.")],
    start: Annotated[float, typer.Option("--start", help="Range start in seconds.")],
    end: Annotated[float, typer.Option("--end", help="Range end in seconds.")],
    track_mic: int = typer.Option(1, "--track-mic", help="0-based mic track (OBS mic track = 1)."),
    model: str | None = typer.Option(None, "--model", help="Force a Whisper model."),
    device: str | None = typer.Option(None, "--device", help="Force cuda or cpu."),
    compute_type: str | None = typer.Option(None, "--compute-type", help="Force compute type."),
    glossary_file: Annotated[
        Path | None,
        typer.Option("--glossary", help='JSON file: {"terms": [...], "replacements": {...}}.'),
    ] = None,
    as_json: bool = typer.Option(False, "--json", help="Print the transcript as JSON."),
) -> None:
    """Transcribe one range of the mic track in Spanish with word timestamps."""
    err = Console(stderr=True)
    began = time.perf_counter()
    try:
        glossary = (
            Glossary.model_validate_json(glossary_file.read_text(encoding="utf-8"))
            if glossary_file
            else Glossary()
        )
        transcript = transcribe_range(
            file,
            track_mic,
            start,
            end,
            glossary,
            pick_device(model, device, compute_type),
            lambda msg: err.print(f"[yellow]warning:[/] {escape(msg)}"),
        )
    except (*_ANALYSIS_ERRORS, RuntimeError) as exc:
        err.print(f"[red]error:[/] {escape(str(exc))}")
        raise typer.Exit(1) from exc
    if as_json:
        typer.echo(transcript.model_dump_json(indent=2))
        return
    console = Console()
    console.print(escape(transcript.text) or "[dim](no speech)[/]")
    console.print(
        f"[dim]{len(transcript.words)} words · {transcript.model} · "
        f"{time.perf_counter() - began:.1f} s[/]"
    )


ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}


@app.command("subs")
def subs_cmd(  # noqa: PLR0913, PLR0917
    transcript_file: Annotated[
        Path | None, typer.Option("--transcript", help="Transcript JSON from `transcribe --json`.")
    ] = None,
    style: str = typer.Option("shorts", "--style", help="Template name (see --list-styles)."),
    aspect: str = typer.Option("9:16", "--aspect", help="9:16 (1080x1920) or 16:9 (1920x1080)."),
    clip_start: float = typer.Option(0.0, "--clip-start", help="Clip start in seconds."),
    output: Annotated[
        Path | None, typer.Option("-o", "--output", help="Output .ass or .srt file.")
    ] = None,
    list_styles: bool = typer.Option(False, "--list-styles", help="List templates and exit."),
) -> None:
    """Turn a transcript into styled .ass subtitles (or .srt) for one clip."""
    if list_styles:
        for key, tpl in TEMPLATES.items():
            typer.echo(f"{key}	{tpl.name} · {tpl.font} · {tpl.mode}")
        return
    err = Console(stderr=True)
    if transcript_file is None or output is None:
        err.print("[red]error:[/] --transcript and -o are required")
        raise typer.Exit(2)
    if style not in TEMPLATES or aspect not in ASPECTS:
        err.print(f"[red]error:[/] unknown style or aspect ({', '.join(TEMPLATES)}; 9:16, 16:9)")
        raise typer.Exit(2)
    try:
        transcript = Transcript.model_validate_json(transcript_file.read_text(encoding="utf-8"))
        if output.suffix.lower() == ".srt":
            output.write_text(build_srt(transcript, TEMPLATES[style], clip_start), "utf-8")
        else:
            width, height = ASPECTS[aspect]
            build_ass(transcript, TEMPLATES[style], width, height, clip_start).save(str(output))
    except (OSError, ValueError) as exc:
        err.print(f"[red]error:[/] {escape(str(exc))}")
        raise typer.Exit(1) from exc
    typer.echo(str(output))


AspectOpt = Annotated[str, typer.Option("--aspect", help="9:16 (1080x1920) or 16:9.")]
CropModeOpt = Annotated[str, typer.Option("--crop-mode", help="9:16 framing: crop or blur.")]
CropXOpt = Annotated[float, typer.Option("--crop-x", help="Crop position: 0 left, 1 right.")]
StyleOpt = Annotated[str, typer.Option("--style", help="Subtitle template, or none.")]
MicOpt = Annotated[int, typer.Option("--track-mic", help="0-based mic track.")]
GameOpt = Annotated[int, typer.Option("--track-game", help="0-based game track.")]
MicVolOpt = Annotated[float, typer.Option("--mic-volume")]
GameVolOpt = Annotated[float, typer.Option("--game-volume")]
NormOpt = Annotated[bool, typer.Option("--normalize/--no-normalize", help="Loudnorm -14 LUFS.")]
EncoderOpt = Annotated[str | None, typer.Option("--encoder", help="Force an FFmpeg encoder.")]
GlossaryOpt = Annotated[
    Path | None,
    typer.Option("--glossary", help='JSON file: {"terms": [...], "replacements": {...}}.'),
]
OutDirOpt = Annotated[Path, typer.Option("-o", "--output", help="Output folder.")]


def _fail(message: str, code: int = 2) -> typer.Exit:
    Console(stderr=True).print(f"[red]error:[/] {escape(message)}")
    return typer.Exit(code)


def _clip_settings(  # noqa: PLR0913, PLR0917
    aspect: str,
    crop_mode: str,
    crop_x: float,
    style: str,
    track_mic: int,
    track_game: int,
    mic_volume: float,
    game_volume: float,
    normalize: bool,
    encoder: str | None,
) -> dict[str, Any]:
    """Validate the options shared by ``clip`` and ``batch``; return ClipOptions fields."""
    if style != "none" and style not in TEMPLATES:
        raise _fail(f"unknown style {style} (choose {', '.join(TEMPLATES)} or none)")
    settings: dict[str, Any] = {
        "aspect": aspect,
        "crop_mode": crop_mode,
        "crop_x": crop_x,
        "style": TEMPLATES.get(style),
        "mic_track": track_mic,
        "game_track": track_game,
        "mic_volume": mic_volume,
        "game_volume": game_volume,
        "normalize": normalize,
        "encoder": encoder,
    }
    try:
        ClipOptions(start_s=0, end_s=0, **settings)
    except ValidationError as exc:
        raise _fail(str(exc)) from exc
    return settings


def _export_all(
    file: Path,
    clips: list[ClipSpec],
    settings: dict[str, Any],
    out_dir: Path,
    glossary: Path | None,
) -> None:
    """Export each clip; a failure is reported and the next clip still runs."""
    err = Console(stderr=True)
    try:
        terms = (
            Glossary.model_validate_json(glossary.read_text(encoding="utf-8"))
            if glossary
            else Glossary()
        )
    except (OSError, ValueError) as exc:
        raise _fail(f"glossary: {exc}") from exc
    choice = pick_device() if settings["style"] is not None else None
    table = Table(title="Clips")
    for col in ("#", "clip", "result"):
        table.add_column(col)
    failed = 0
    with Progress(console=err, transient=True) as bar:
        task = bar.add_task("", total=1.0)
        for i, spec in enumerate(clips, 1):
            bar.reset(task, description=f"{i}/{len(clips)} {escape(spec.name)}")
            began = time.perf_counter()
            try:
                out = export_clip(
                    file,
                    ClipOptions(start_s=spec.start_s, end_s=spec.end_s, **settings),
                    out_dir,
                    spec.name,
                    terms,
                    choice,
                    lambda p: bar.update(task, completed=p),
                    on_warning=lambda msg: err.print(f"[yellow]warning:[/] {escape(msg)}"),
                )
                result = f"[green]ok[/] {escape(str(out))} ({time.perf_counter() - began:.0f} s)"
            except (*_ANALYSIS_ERRORS, RuntimeError) as exc:
                failed += 1
                result = f"[red]failed:[/] {escape(str(exc).strip())}"
            table.add_row(str(i), escape(spec.name), result)
    Console().print(table)
    Console().print(f"{len(clips) - failed} exported, {failed} failed")
    if failed:
        raise typer.Exit(1)


@app.command("clip")
def clip_cmd(  # noqa: PLR0913, PLR0917
    file: Annotated[Path, typer.Argument(help="Recording to cut.")],
    start: Annotated[str, typer.Option("--start", help="Start: seconds or mm:ss.")],
    end: Annotated[str, typer.Option("--end", help="End: seconds or mm:ss.")],
    output: OutDirOpt = Path(),
    name: Annotated[str | None, typer.Option("--name", help="Clip name for the file.")] = None,
    aspect: AspectOpt = "9:16",
    crop_mode: CropModeOpt = "crop",
    crop_x: CropXOpt = 0.5,
    style: StyleOpt = "shorts",
    track_mic: MicOpt = 1,
    track_game: GameOpt = 2,
    mic_volume: MicVolOpt = _RENDER.mic_volume,
    game_volume: GameVolOpt = _RENDER.game_volume,
    normalize: NormOpt = True,
    encoder: EncoderOpt = None,
    glossary: GlossaryOpt = None,
) -> None:
    """Export one clip (15-60 s) as MP4 with burned subtitles."""
    settings = _clip_settings(
        aspect,
        crop_mode,
        crop_x,
        style,
        track_mic,
        track_game,
        mic_volume,
        game_volume,
        normalize,
        encoder,
    )
    try:
        start_s, end_s = parse_time(start), parse_time(end)
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    clip_name = name or f"{start.replace(':', '.')}-{end.replace(':', '.')}"
    _export_all(file, [ClipSpec(0, start_s, end_s, clip_name)], settings, output, glossary)


@app.command("batch")
def batch_cmd(  # noqa: PLR0913, PLR0917
    file: Annotated[Path, typer.Argument(help="Recording to cut.")],
    clips: Annotated[Path, typer.Option("--clips", help="Lines of: start end name.")],
    output: OutDirOpt = Path(),
    aspect: AspectOpt = "9:16",
    crop_mode: CropModeOpt = "crop",
    crop_x: CropXOpt = 0.5,
    style: StyleOpt = "shorts",
    track_mic: MicOpt = 1,
    track_game: GameOpt = 2,
    mic_volume: MicVolOpt = _RENDER.mic_volume,
    game_volume: GameVolOpt = _RENDER.game_volume,
    normalize: NormOpt = True,
    encoder: EncoderOpt = None,
    glossary: GlossaryOpt = None,
) -> None:
    """Export every clip listed in a clips file (validated as a whole before starting)."""
    settings = _clip_settings(
        aspect,
        crop_mode,
        crop_x,
        style,
        track_mic,
        track_game,
        mic_volume,
        game_volume,
        normalize,
        encoder,
    )
    try:
        specs = parse_clips_file(clips.read_text(encoding="utf-8-sig"), probe(file).duration_s)
    except ClipsFileError as exc:
        raise _fail(f"{clips}:\n{exc}") from exc
    except (OSError, MediaError) as exc:
        raise _fail(str(exc), 1) from exc
    _export_all(file, specs, settings, output, glossary)


@app.command("serve")
def serve_cmd(
    token: Annotated[str, typer.Option("--token", help="Per-session secret for every request.")],
    port: int = typer.Option(0, "--port", help="Port on 127.0.0.1; 0 picks a free one."),
) -> None:
    """Run the local API; prints {"ready": true, "port": N} on stdout when it accepts requests."""
    from clipapp_engine.api.server import serve  # noqa: PLC0415 (keeps other commands fast)

    if not token:
        raise _fail("--token must not be empty")
    try:
        serve(port, token)
    except OSError as exc:
        raise _fail(f"cannot listen on 127.0.0.1:{port}: {exc}", 1) from exc


@app.command("openapi")
def openapi_cmd(
    output: Annotated[Path | None, typer.Option("-o", "--output", help="Output file.")] = None,
    markdown: bool = typer.Option(False, "--markdown", help="Write docs/api.md style Markdown."),
) -> None:
    """Print (or write) the API's OpenAPI JSON, or its Markdown endpoint reference."""
    from clipapp_engine.api.docs import api_markdown, openapi_schema  # noqa: PLC0415

    schema = openapi_schema()
    text = api_markdown(schema) if markdown else json.dumps(schema, indent=2) + "\n"
    if output is None:
        typer.echo(text, nl=False)
    else:
        output.write_text(text, encoding="utf-8", newline="\n")
