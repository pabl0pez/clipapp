from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from clipapp_engine.media.errors import (
    MediaNotFoundError,
    NoAudioStreamError,
    NoVideoStreamError,
    ProbeError,
)
from clipapp_engine.media.probe import parse_probe, probe


def _check_sample(path: Path) -> None:
    info = probe(path)
    assert info.path == path
    assert info.duration_s == pytest.approx(20, abs=0.1)
    assert info.fps == Fraction(30)
    assert not info.is_vfr
    assert (info.width, info.height) == (640, 360)
    assert info.video_codec == "h264"
    assert info.pix_fmt == "yuv420p"
    assert info.rotation == 0
    assert [t.title for t in info.audio_tracks] == ["Mix", "Mic", "Game"]
    assert [t.index for t in info.audio_tracks] == [0, 1, 2]
    assert [t.stream_index for t in info.audio_tracks] == [1, 2, 3]
    assert all(t.codec == "aac" and t.sample_rate == 48000 for t in info.audio_tracks)


def test_probe_sample(av_sample: Path) -> None:
    _check_sample(av_sample)


def test_probe_accented_path(accented_sample: Path) -> None:
    _check_sample(accented_sample)


def test_probe_missing(tmp_path: Path) -> None:
    with pytest.raises(MediaNotFoundError):
        probe(tmp_path / "nope.mkv")


def test_probe_no_audio(video_no_audio: Path) -> None:
    with pytest.raises(NoAudioStreamError):
        probe(video_no_audio)


def test_probe_no_video(audio_only: Path) -> None:
    with pytest.raises(NoVideoStreamError):
        probe(audio_only)


def test_probe_garbage_file(tmp_path: Path) -> None:
    bad = tmp_path / "not video.mkv"
    bad.write_text("hello")
    with pytest.raises(ProbeError) as exc:
        probe(bad)
    assert exc.value.stderr_tail


def _data(video: dict[str, Any]) -> dict[str, Any]:
    base = {"codec_type": "video", "index": 0, "width": 1920, "height": 1080}
    audio = {"codec_type": "audio", "index": 1, "codec_name": "aac", "tags": {"TITLE": "Mic"}}
    return {"format": {"duration": "10.5"}, "streams": [base | video, audio]}


def test_parse_vfr_and_rotation() -> None:
    video = {
        "avg_frame_rate": "59/1",
        "r_frame_rate": "60/1",
        "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}],
    }
    info = parse_probe(Path("x.mp4"), _data(video))
    assert info.is_vfr
    assert info.fps == Fraction(59)
    assert info.rotation == 90
    assert info.audio_tracks[0].title == "Mic"


def test_parse_zero_avg_rate_and_rotate_tag() -> None:
    video = {"avg_frame_rate": "0/0", "r_frame_rate": "30000/1001", "tags": {"rotate": "180"}}
    info = parse_probe(Path("x.mov"), _data(video))
    assert info.fps == Fraction(30000, 1001)
    assert not info.is_vfr
    assert info.rotation == 180


def test_parse_ignores_cover_art() -> None:
    data = _data({"avg_frame_rate": "30/1"})
    data["streams"][0]["disposition"] = {"attached_pic": 1}
    with pytest.raises(NoVideoStreamError):
        parse_probe(Path("x.mkv"), data)
