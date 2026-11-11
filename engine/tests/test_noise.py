from pathlib import Path

from clipapp_engine.silence.noise import suggest_threshold


def test_clean_mic_track(mic_wav: Path) -> None:
    s = suggest_threshold(mic_wav)
    assert s.warning is None
    assert -50 <= s.threshold_db <= -20
    assert s.voice_db - s.floor_db >= 10


def test_mix_track_warns(mix_wav: Path) -> None:
    s = suggest_threshold(mix_wav)
    assert s.warning is not None
    assert -50 <= s.threshold_db <= -20
