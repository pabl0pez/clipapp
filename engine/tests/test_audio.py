import wave
from pathlib import Path

import pytest
from conftest import DURATION_S

from clipapp_engine.media import audio
from clipapp_engine.media.audio import extract_track_wav, track_wav_path
from clipapp_engine.media.errors import MediaError, MediaNotFoundError


def test_extracts_mono_16k_pcm(accented_sample: Path) -> None:
    progress: list[float] = []
    wav = extract_track_wav(accented_sample, 1, progress.append)
    assert wav == track_wav_path(accented_sample, 1)
    assert wav.name == "track1.wav"
    assert not wav.with_suffix(".wav.part").exists()
    with wave.open(str(wav), "rb") as f:
        assert (f.getnchannels(), f.getframerate(), f.getsampwidth()) == (1, 16000, 2)
        assert abs(f.getnframes() / f.getframerate() - DURATION_S) < 0.1
    assert progress[-1] == 1.0


def test_second_run_uses_cache(av_sample: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = extract_track_wav(av_sample, 2)

    def fail(*_: object, **__: object) -> None:
        raise AssertionError("ffmpeg must not run on a cache hit")

    monkeypatch.setattr(audio, "run_ffmpeg", fail)
    progress: list[float] = []
    assert extract_track_wav(av_sample, 2, progress.append) == first
    assert progress == [1.0]


def test_invalid_track(av_sample: Path) -> None:
    with pytest.raises(MediaError, match="track 7 not found"):
        extract_track_wav(av_sample, 7)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(MediaNotFoundError):
        extract_track_wav(tmp_path / "nope.mkv", 1)
