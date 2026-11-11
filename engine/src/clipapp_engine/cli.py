"""Command-line entry point (``clipapp-engine``)."""

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from clipapp_engine.doctor import run_doctor
from clipapp_engine.media.errors import MediaError
from clipapp_engine.media.probe import probe

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
