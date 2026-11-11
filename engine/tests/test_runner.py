import threading
import time
from pathlib import Path

import pytest

from clipapp_engine.media.errors import FFmpegCancelledError, FFmpegError
from clipapp_engine.media.runner import _parse_out_time_s, run_ffmpeg


def test_progress_increases_to_one(av_sample: Path, tmp_path: Path) -> None:
    values: list[float] = []
    out = tmp_path / "Salida ñ" / "clip é.mp4"
    out.parent.mkdir()
    args = ["-i", str(av_sample), "-map", "0:v", "-map", "0:a:1"]
    args += ["-c:v", "libx264", "-preset", "slow", str(out)]
    run_ffmpeg(args, 20, values.append)
    assert out.is_file()
    assert values[-1] == 1.0
    assert values == sorted(values)
    assert len(values) > 1


def test_cancel_midway() -> None:
    cancel = threading.Event()

    def on_progress(value: float) -> None:
        if value > 0.01:
            cancel.set()

    args = ["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=600"]
    args += ["-c:v", "libx264", "-preset", "medium", "-f", "null", "-"]
    start = time.monotonic()
    with pytest.raises(FFmpegCancelledError):
        run_ffmpeg(args, 600, on_progress, cancel)
    assert cancel.is_set()
    assert time.monotonic() - start < 30


def test_failure_has_stderr(tmp_path: Path) -> None:
    with pytest.raises(FFmpegError) as exc:
        run_ffmpeg(["-i", str(tmp_path / "missing ñ.mkv"), str(tmp_path / "o.wav")], 1)
    assert not isinstance(exc.value, FFmpegCancelledError)
    assert "missing ñ.mkv" in exc.value.stderr_tail


def test_parse_out_time() -> None:
    assert _parse_out_time_s("out_time_us=1500000") == 1.5
    assert _parse_out_time_s("out_time_ms=2000000") == 2.0
    assert _parse_out_time_s("out_time_us=N/A") is None
    assert _parse_out_time_s("frame=10") is None
