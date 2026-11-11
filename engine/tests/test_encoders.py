import json
from pathlib import Path

import pytest

from clipapp_engine import config
from clipapp_engine.doctor import EncoderResult
from clipapp_engine.media import encoders


@pytest.fixture
def fake_ffmpeg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Isolated data dir and fake probes; returns the list of probed encoder names."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(config, "ffmpeg_path", lambda: Path("ffmpeg"))
    monkeypatch.setattr(encoders, "tool_version", lambda _: "9.0.2")
    probed: list[str] = []

    def probe(_: Path, name: str) -> EncoderResult:
        probed.append(name)
        return EncoderResult(name, name in ("h264_amf", "libx264"))

    monkeypatch.setattr(encoders, "probe_encoder", probe)
    return probed


def test_cache_is_reused(fake_ffmpeg: list[str]) -> None:
    first = encoders.load_encoder_results()
    second = encoders.load_encoder_results()
    assert first == second
    assert first["h264_amf"] and not first["h264_nvenc"]
    assert fake_ffmpeg == list(encoders.ENCODER_ORDER)
    data = json.loads((config.data_dir() / "encoders.json").read_text(encoding="utf-8"))
    assert data["ffmpeg_version"] == "9.0.2"


def test_cache_invalidated_by_new_ffmpeg(
    fake_ffmpeg: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    encoders.load_encoder_results()
    monkeypatch.setattr(encoders, "tool_version", lambda _: "9.1")
    encoders.load_encoder_results()
    assert len(fake_ffmpeg) == 2 * len(encoders.ENCODER_ORDER)


def test_corrupt_cache_reprobes(fake_ffmpeg: list[str]) -> None:
    (config.data_dir() / "encoders.json").write_text("{nope", encoding="utf-8")
    assert encoders.load_encoder_results()["libx264"]
    assert fake_ffmpeg


def test_doctor_results_are_used(fake_ffmpeg: list[str]) -> None:
    results = [EncoderResult(n, n == "h264_nvenc") for n in encoders.ENCODER_ORDER]
    encoders.save_encoder_cache("9.0.2", results)
    assert encoders.pick_encoder() == "h264_nvenc"
    assert not fake_ffmpeg


@pytest.mark.parametrize(
    ("usable", "expected"),
    [
        ({"h264_nvenc", "h264_amf", "libx264"}, "h264_nvenc"),
        ({"h264_qsv", "libx264"}, "h264_qsv"),
        ({"h264_amf", "libx264"}, "h264_amf"),
        ({"libx264", "libopenh264"}, "libx264"),
        ({"libopenh264"}, "libopenh264"),
    ],
)
def test_pick_order(monkeypatch: pytest.MonkeyPatch, usable: set[str], expected: str) -> None:
    results = {n: n in usable for n in encoders.ENCODER_ORDER}
    monkeypatch.setattr(encoders, "load_encoder_results", lambda: results)
    assert encoders.pick_encoder() == expected


def test_pick_preference(monkeypatch: pytest.MonkeyPatch) -> None:
    results = {n: n in ("h264_amf", "libx264") for n in encoders.ENCODER_ORDER}
    monkeypatch.setattr(encoders, "load_encoder_results", lambda: results)
    assert encoders.pick_encoder("libx264") == "libx264"
    with pytest.raises(encoders.EncoderError, match="not usable"):
        encoders.pick_encoder("h264_nvenc")
    with pytest.raises(encoders.EncoderError, match="Unknown"):
        encoders.pick_encoder("prores")


def test_pick_none_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(encoders, "load_encoder_results", lambda: {})
    with pytest.raises(encoders.EncoderError, match="No usable"):
        encoders.pick_encoder()


def test_encoder_args() -> None:
    for name in encoders.ENCODER_ORDER:
        args = encoders.video_encoder_args(name)
        assert args[:2] == ["-c:v", name]
        assert "-pix_fmt" in args
    assert "-cq" in encoders.video_encoder_args("h264_nvenc")
    assert encoders.video_encoder_args("libx264")[-1] == "yuv420p"
