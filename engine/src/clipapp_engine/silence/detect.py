"""Silence detection with FFmpeg's ``silencedetect`` filter."""

import re
import threading
import wave
from collections.abc import Callable, Iterable
from pathlib import Path

from clipapp_engine.media.runner import run_ffmpeg

_EVENT = re.compile(r"silence_(start|end):\s*(-?\d+(?:\.\d+)?(?:e[-+]?\d+)?)")


def wav_duration_s(wav: Path) -> float:
    """Return the duration of a PCM WAV from its header.

    Raises:
        OSError: the file cannot be opened.
        wave.Error: the file is not a PCM WAV.
    """
    with wave.open(str(wav), "rb") as f:
        return f.getnframes() / f.getframerate()


def parse_silences(lines: Iterable[str], duration_s: float) -> list[tuple[float, float]]:
    """Turn ``silencedetect`` stderr lines into sorted ``(start_s, end_s)`` ranges.

    Args:
        lines: ffmpeg stderr lines; non-matching lines are ignored.
        duration_s: Total duration, closing a trailing silence that has no ``silence_end``.

    Returns:
        Silences sorted by start, clamped to ``[0, duration_s]``.
    """
    silences: list[tuple[float, float]] = []
    start: float | None = None
    for line in lines:
        for kind, value in _EVENT.findall(line):
            t = min(max(float(value), 0.0), duration_s)
            if kind == "start":
                start = t
            elif start is not None:
                silences.append((start, t))
                start = None
    if start is not None and start < duration_s:
        silences.append((start, duration_s))
    return sorted(silences)


def detect_silences(
    wav: Path,
    threshold_db: float,
    min_silence_s: float,
    on_progress: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> list[tuple[float, float]]:
    """Detect silences in a WAV with ``silencedetect=noise=<threshold>dB:d=<min_silence>``.

    Args:
        wav: Analysis WAV (see ``extract_track_wav``).
        threshold_db: Silence level in dBFS.
        min_silence_s: Minimum silence length in seconds.
        on_progress: Progress in [0, 1].
        cancel_event: Cancels the run.

    Returns:
        Sorted ``(start_s, end_s)`` silences; a silence reaching the end closes at the duration.

    Raises:
        OSError: the WAV cannot be opened.
        wave.Error: the file is not a PCM WAV.
        FFmpegError: ffmpeg failed or was cancelled.
    """
    duration = wav_duration_s(wav)
    lines: list[str] = []
    args = ["-i", str(wav), "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence_s}"]
    run_ffmpeg([*args, "-f", "null", "-"], duration, on_progress, cancel_event, lines.append)
    return parse_silences(lines, duration)
