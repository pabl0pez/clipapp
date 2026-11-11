import json
import subprocess
import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import DURATION_S, MIC_SILENCES
from typer.testing import CliRunner

from clipapp_engine.cli import app
from clipapp_engine.config import ffprobe_path
from clipapp_engine.media import audio, proxy, thumbnails
from clipapp_engine.media.prepare import FORMAT_VERSION, Manifest, prepare
from clipapp_engine.media.runner import run_ffmpeg
from clipapp_engine.media.waveform import build_waveform


def _ffprobe(path: Path, *args: str) -> dict[str, Any]:
    cmd = [str(ffprobe_path()), "-v", "error", *args, "-of", "json", str(path)]
    result: dict[str, Any] = json.loads(subprocess.run(cmd, check=True, capture_output=True).stdout)
    return result


@pytest.fixture(scope="module")
def manifest(av_sample: Path) -> Path:
    return prepare(av_sample, 1)


def test_proxy_format(manifest: Path) -> None:
    path = manifest.parent / "proxy.mp4"
    streams = {s["codec_type"]: s for s in _ffprobe(path, "-show_streams")["streams"]}
    video, audio_stream = streams["video"], streams["audio"]
    assert (video["width"], video["height"]) == (1280, 720)
    assert video["codec_name"] == "h264"
    assert video["avg_frame_rate"] == "30/1"
    assert audio_stream["codec_name"] == "aac"
    assert audio_stream["channels"] == 2
    packets = _ffprobe(path, "-select_streams", "v", "-show_entries", "packet=pts,flags")
    ordered = sorted(packets["packets"], key=lambda p: int(p["pts"]))
    keys = [i for i, p in enumerate(ordered) if "K" in p["flags"]]
    assert keys == list(range(0, len(ordered), 15))


def test_waveform(manifest: Path) -> None:
    doc = json.loads((manifest.parent / "waveform.json").read_text(encoding="utf-8"))
    data = np.array(doc["data"])
    assert doc["duration"] == pytest.approx(DURATION_S, abs=0.05)
    assert doc["peaks_per_second"] == 50
    assert len(data) == round(doc["duration"] * 50) * 2 == doc["length"] * 2
    assert data.min() >= -128 and data.max() <= 127
    silent = int(np.mean(MIC_SILENCES[0]) * 50) * 2
    loud = 1 * 50 * 2
    assert abs(data[silent + 1]) <= 1 and data[loud + 1] >= 15  # sine at 1/8 full scale


def test_waveform_partial_bucket(tmp_path: Path) -> None:
    wav = tmp_path / "w.wav"
    samples = np.zeros(321, dtype="<i2")
    samples[-1] = 32767
    with wave.open(str(wav), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16_000)
        f.writeframes(samples.tobytes())
    doc = json.loads(build_waveform(wav).read_text(encoding="utf-8"))
    assert doc["data"] == [0, 0, 127, 127]


def test_sprite(manifest: Path) -> None:
    index = json.loads((manifest.parent / "thumbnails.json").read_text(encoding="utf-8"))
    assert index == {
        "interval_s": 10,
        "width": 160,
        "height": 90,
        "columns": 10,
        "rows": 1,
        "count": DURATION_S // 10,
    }
    sprite = _ffprobe(manifest.parent / "sprite.jpg", "-show_streams")["streams"][0]
    assert (sprite["width"], sprite["height"]) == (1600, 90)


def test_manifest(manifest: Path, av_sample: Path) -> None:
    data = Manifest.model_validate_json(manifest.read_text(encoding="utf-8"))
    assert data.version == FORMAT_VERSION
    assert data.mic_track == 1
    assert Path(data.source) == av_sample.resolve()


def test_cache_and_force(manifest: Path, av_sample: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def counting(args: list[str], *rest: Any, **kw: Any) -> None:
        calls.append(args)
        run_ffmpeg(args, *rest, **kw)

    for module in (audio, proxy, thumbnails):
        monkeypatch.setattr(module, "run_ffmpeg", counting)
    progress: list[tuple[str, float]] = []
    prepare(av_sample, 1, lambda s, p: progress.append((s, p)))
    assert calls == []
    assert progress[-1][0] == "thumbnails" and progress[-1][1] == pytest.approx(1.0)
    assert [p for _, p in progress] == sorted(p for _, p in progress)

    result = CliRunner().invoke(app, ["prepare", str(av_sample), "--track-mic", "1", "--force"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 3
    assert "proxy.mp4" in result.output


def test_prepare_missing_file(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["prepare", str(tmp_path / "nope.mkv")])
    assert result.exit_code == 1
