"""FFmpeg process runner with progress reporting and cancellation."""

import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path

from clipapp_engine.config import ffmpeg_path
from clipapp_engine.media.errors import FFmpegCancelledError, FFmpegError

# Keeps console windows from popping up when the engine runs without one.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
STDERR_LINES = 40
KILL_GRACE_S = 3.0


def _parse_out_time_s(line: str) -> float | None:
    """Return seconds from an ``out_time_us=``/``out_time_ms=`` progress line, else None."""
    key, _, value = line.partition("=")
    # Both keys hold microseconds (out_time_ms is misnamed in FFmpeg).
    if key not in ("out_time_us", "out_time_ms"):
        return None
    try:
        return int(value) / 1_000_000
    except ValueError:  # "N/A" before the first frame
        return None


def _pump_lines(
    stream: Iterable[str], tail: deque[str], on_line: Callable[[str], None] | None
) -> None:
    """Keep the last lines of a stream in ``tail`` and pass each one to ``on_line``."""
    for raw in stream:
        line = raw.rstrip()
        tail.append(line)
        if on_line is not None:
            on_line(line)


def run_ffmpeg(  # noqa: PLR0913, PLR0917
    args: list[str],
    duration_s: float,
    on_progress: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
    on_stderr_line: Callable[[str], None] | None = None,
    cwd: Path | None = None,
) -> None:
    """Run ffmpeg to completion, reporting progress and honoring cancellation.

    Args:
        args: ffmpeg arguments after the executable (inputs, filters, outputs). The runner adds
            ``-hide_banner -nostdin -y -nostats -progress pipe:1`` itself.
        duration_s: Expected output duration, used to scale progress to 0..1.
        on_progress: Called with non-decreasing values in [0, 1]; gets 1.0 on success.
        cancel_event: When set, ffmpeg is terminated (killed after 3 s if still alive).
        on_stderr_line: Called from a reader thread with every stderr line (newline stripped),
            for filters that log results there (e.g. ``silencedetect``).
        cwd: Working directory, so filter options can use simple relative file names.

    Raises:
        FFmpegCancelledError: the run was cancelled.
        FFmpegError: ffmpeg could not start or exited non-zero (includes the stderr tail).
    """
    cmd = [
        str(ffmpeg_path()),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-nostats",
        "-progress",
        "pipe:1",
        *args,
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
            cwd=cwd,
        )
    except OSError as exc:
        raise FFmpegError(f"ffmpeg could not start: {exc}") from exc
    assert proc.stdout is not None and proc.stderr is not None

    tail: deque[str] = deque(maxlen=STDERR_LINES)
    stderr = proc.stderr
    reader = threading.Thread(target=_pump_lines, args=(stderr, tail, on_stderr_line))
    reader.start()

    cancelled = threading.Event()
    done = threading.Event()

    def watch_cancel() -> None:
        assert cancel_event is not None
        while not done.is_set():
            if cancel_event.wait(0.1):
                cancelled.set()
                proc.terminate()
                try:
                    proc.wait(KILL_GRACE_S)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return

    watcher = threading.Thread(target=watch_cancel) if cancel_event is not None else None
    if watcher is not None:
        watcher.start()

    last = 0.0
    try:
        for line in proc.stdout:
            seconds = _parse_out_time_s(line.strip())
            if seconds is None or on_progress is None or duration_s <= 0:
                continue
            value = min(max(seconds / duration_s, 0.0), 1.0)
            if value > last:
                last = value
                on_progress(value)
        returncode = proc.wait()
    finally:
        done.set()
        if watcher is not None:
            watcher.join()
        reader.join()

    stderr_tail = "\n".join(tail)
    if cancelled.is_set():
        raise FFmpegCancelledError("ffmpeg was cancelled", stderr_tail)
    if returncode != 0:
        raise FFmpegError(f"ffmpeg failed (exit {returncode})", stderr_tail)
    if on_progress is not None and last < 1.0:
        on_progress(1.0)
