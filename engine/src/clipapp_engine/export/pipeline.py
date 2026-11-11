"""Export a clip end to end: transcription (cached) + glossary, subtitles and render."""

import re
import threading
from collections.abc import Callable
from pathlib import Path

from clipapp_engine.export.clip import ClipOptions, render_clip, validate_range
from clipapp_engine.media.probe import probe
from clipapp_engine.transcribe.device import DeviceChoice
from clipapp_engine.transcribe.glossary import Glossary
from clipapp_engine.transcribe.transcript import Transcript
from clipapp_engine.transcribe.whisper import transcribe_range

TRANSCRIBE_SHARE = 0.3  # progress share of transcription (it reports no progress of its own)
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}


def safe_name(text: str) -> str:
    """Return ``text`` usable as part of a Windows file name (accents and spaces are kept)."""
    name = _UNSAFE.sub("_", text).strip(" .") or "clip"
    return f"_{name}" if name.split(".")[0].upper() in _RESERVED else name


def output_path(out_dir: Path, video: Path, clip_name: str, aspect: str) -> Path:
    """Return ``{video}_{clip}_{aspect}.mp4`` in ``out_dir``, adding ``_2``, ``_3``... if taken."""
    stem = "_".join(safe_name(p) for p in (video.stem, clip_name, aspect.replace(":", "x")))
    path, n = out_dir / f"{stem}.mp4", 2
    while path.exists():
        path, n = out_dir / f"{stem}_{n}.mp4", n + 1
    return path


def export_clip(  # noqa: PLR0913, PLR0917
    media: Path,
    options: ClipOptions,
    out_dir: Path,
    clip_name: str,
    glossary: Glossary | None = None,
    choice: DeviceChoice | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
    on_warning: Callable[[str], None] | None = None,
    transcript: Transcript | None = None,
) -> Path:
    """Transcribe the clip's mic audio (when it has a style), then render it into ``out_dir``.

    Transcripts come from the Stage 5 cache when available and already carry the glossary
    replacements.

    Args:
        media: Source recording (never modified).
        options: Clip settings; ``style=None`` skips transcription and subtitles.
        out_dir: Output folder (created if missing).
        clip_name: Used in the output file name.
        glossary: Prompt terms and replacements.
        choice: Whisper model/device; None auto-detects.
        on_progress: Combined progress in [0, 1].
        cancel_event: Cancels the render.
        on_warning: Receives transcription degradation notices.
        transcript: Words to burn (e.g. user-edited); given, transcription is skipped.

    Returns:
        The written MP4.

    Raises:
        ValueError: invalid range.
        MediaError, EncoderError, FFmpegError, OSError, RuntimeError: see ``transcribe_range``
            and ``render_clip``.
    """
    validate_range(options.start_s, options.end_s, probe(media).duration_s)
    report = on_progress or (lambda _p: None)
    share = 0.0
    if options.style is not None and transcript is None:
        transcript = transcribe_range(
            media, options.mic_track, options.start_s, options.end_s, glossary, choice, on_warning
        )
        share = TRANSCRIBE_SHARE
        report(share)
    out = output_path(out_dir, media, clip_name, options.aspect)
    render_clip(
        media, options, out, transcript, lambda p: report(share + p * (1 - share)), cancel_event
    )
    return out
