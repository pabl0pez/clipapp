import json
import subprocess
import threading
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from clipapp_engine.config import ffprobe_path
from clipapp_engine.export.full import (
    RenderOptions,
    build_filtergraph,
    render_without_silences,
    snap_ranges,
)
from clipapp_engine.media.errors import FFmpegCancelledError, MediaError
from clipapp_engine.media.probe import probe

RANGES = [(0.0, 2.5), (5.2, 9.0), (10.0, 11.7), (15.1, 20.0)]
KEPT_S = sum(e - s for s, e in RANGES)
FRAME_S = 1 / 30
OPTIONS = RenderOptions(encoder="libx264")


def _streams(path: Path) -> dict[str, dict[str, Any]]:
    flags = ["-v", "error", "-show_format", "-show_streams", "-of", "json"]
    proc = subprocess.run(
        [str(ffprobe_path()), *flags, str(path)],
        check=True,
        capture_output=True,
    )
    data = json.loads(proc.stdout)
    out = {s["codec_type"]: s for s in data["streams"]}
    out["format"] = data["format"]
    return out


@pytest.fixture(scope="module")
def direct(av_sample: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("direct") / "Sin silencios ñ.mp4"
    assert render_without_silences(av_sample, RANGES, OPTIONS, out) == "libx264"
    return out


def test_duration_matches_ranges(direct: Path) -> None:
    info = _streams(direct)
    assert abs(float(info["format"]["duration"]) - KEPT_S) < 0.1
    assert info["video"]["codec_name"] == "h264"
    assert info["video"]["pix_fmt"] == "yuv420p"
    assert (info["video"]["width"], info["video"]["height"]) == (640, 360)
    assert info["video"]["avg_frame_rate"] == "30/1"
    assert info["audio"]["codec_name"] == "aac"
    assert info["audio"]["channels"] == 2


def test_audio_video_in_sync(direct: Path) -> None:
    info = _streams(direct)
    gap = abs(float(info["video"]["duration"]) - float(info["audio"]["duration"]))
    assert gap < FRAME_S


def test_batches_match_direct(av_sample: Path, direct: Path, tmp_path: Path) -> None:
    progress: list[float] = []
    out = tmp_path / "batched.mp4"
    render_without_silences(av_sample, RANGES, OPTIONS, out, progress.append, batch_size=1)
    a, b = _streams(direct), _streams(out)
    assert a["video"]["nb_frames"] == b["video"]["nb_frames"]
    for kind in ("video", "audio"):
        assert abs(float(a[kind]["duration"]) - float(b[kind]["duration"])) < FRAME_S
    assert progress == sorted(progress)
    assert progress[-1] == pytest.approx(1.0)
    assert [p.name for p in tmp_path.iterdir()] == ["batched.mp4"]


def test_cancel_leaves_no_temp_files(av_sample: Path, tmp_path: Path) -> None:
    cancel = threading.Event()

    def on_progress(value: float) -> None:
        if value > 0:
            cancel.set()

    out = tmp_path / "cancelled.mp4"
    with pytest.raises(FFmpegCancelledError):
        render_without_silences(av_sample, RANGES, OPTIONS, out, on_progress, cancel, 1)
    assert list(tmp_path.iterdir()) == []


def test_bad_track(av_sample: Path, tmp_path: Path) -> None:
    with pytest.raises(MediaError, match="track 7"):
        render_without_silences(av_sample, RANGES, RenderOptions(game_track=7), tmp_path / "x.mp4")


def test_snap_ranges() -> None:
    assert snap_ranges([(0.0, 1.0), (1.51, 1.515), (2.49, 3.0)], Fraction(30)) == [
        (0, 30),
        (75, 90),
    ]


def test_filtergraph_vfr_mix_and_fades(av_sample: Path) -> None:
    info = probe(av_sample).model_copy(update={"is_vfr": True})
    graph = build_filtergraph(info, [(0, 30), (60, 90)], RenderOptions(game_volume=0.5))
    assert graph.startswith("[0:v:0]fps=30,split=2[v0][v1]")
    assert "amix=inputs=2:normalize=0" in graph
    assert "volume=0.5[game]" in graph
    assert graph.count("afade=t=in:d=0.01") == 2
    assert "afade=t=out:st=0.990000:d=0.01" in graph
    assert "concat=n=2:v=1:a=1[v][a]" in graph
