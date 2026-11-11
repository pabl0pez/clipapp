import subprocess
from pathlib import Path

import pytest
from conftest import MIC_SILENCES

from clipapp_engine.config import ffmpeg_path
from clipapp_engine.silence.detect import detect_silences, parse_silences, wav_duration_s

TOLERANCE_S = 0.05


def test_detects_fixture_silences(mic_wav: Path) -> None:
    silences = detect_silences(mic_wav, -35, 0.5)
    assert len(silences) == len(MIC_SILENCES)
    for (start, end), (want_start, want_end) in zip(silences, MIC_SILENCES, strict=True):
        assert start == pytest.approx(want_start, abs=TOLERANCE_S)
        assert end == pytest.approx(want_end, abs=TOLERANCE_S)


def test_trailing_silence_closes_at_duration(tmp_path: Path) -> None:
    wav = tmp_path / "tail silence.wav"
    sine = "sine=frequency=300:sample_rate=16000:samples_per_frame=160:duration=4"
    graph = f"{sine},volume=0:enable='gte(t,2.5)'"
    subprocess.run(
        [str(ffmpeg_path()), "-v", "error", "-f", "lavfi", "-i", graph, "-c:a", "pcm_s16le", wav],
        check=True,
    )
    silences = detect_silences(wav, -35, 0.5)
    assert len(silences) == 1
    assert silences[0][0] == pytest.approx(2.5, abs=TOLERANCE_S)
    assert silences[0][1] == wav_duration_s(wav)


def test_parse_silences() -> None:
    lines = [
        "[silencedetect @ 000001] silence_start: 7.5",
        "noise line",
        "[silencedetect @ 000001] silence_end: 9 | silence_duration: 1.5",
        "[silencedetect @ 000001] silence_start: -0.01",
        "[silencedetect @ 000001] silence_end: 1.25 | silence_duration: 1.26",
        "[silencedetect @ 000001] silence_start: 18.2",
    ]
    assert parse_silences(lines, 20.0) == [(0.0, 1.25), (7.5, 9.0), (18.2, 20.0)]
    assert parse_silences([], 20.0) == []
