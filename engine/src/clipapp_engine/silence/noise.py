"""Silence threshold suggestion from the level distribution of a track."""

import wave
from pathlib import Path

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

WINDOW_S = 0.05
BLOCK_WINDOWS = 200  # 10 s per read keeps memory flat on multi-hour WAVs
FLOOR_DB = -100.0  # stands in for digital silence (-inf dBFS)
MARGIN_DB = 6.0
MIN_GAP_DB = 10.0
THRESHOLD_MIN_DB, THRESHOLD_MAX_DB = -50.0, -20.0
NOISY_WARNING = (
    "The track has constant background noise (game audio is probably mixed in); "
    "use the mic-only track for better cuts."
)


class ThresholdSuggestion(BaseModel):
    """Suggested ``silencedetect`` threshold and the levels it came from (dBFS)."""

    threshold_db: float
    floor_db: float
    voice_db: float
    warning: str | None = None


def window_rms_db(wav: Path) -> npt.NDArray[np.float64]:
    """Return the RMS level in dBFS of each 50 ms window, reading the WAV in blocks.

    Raises:
        ValueError: the WAV is not 16-bit PCM.
        OSError: the file cannot be opened.
        wave.Error: the file is not a PCM WAV.
    """
    with wave.open(str(wav), "rb") as f:
        if f.getsampwidth() != 2:
            raise ValueError(f"{wav} is not 16-bit PCM")
        span = max(int(f.getframerate() * WINDOW_S), 1) * f.getnchannels()
        levels: list[npt.NDArray[np.float64]] = []
        while frames := f.readframes(span // f.getnchannels() * BLOCK_WINDOWS):
            samples = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
            usable = len(samples) // span * span
            if usable == 0:
                break
            power = np.mean(samples[:usable].reshape(-1, span) ** 2, axis=1)
            levels.append(10 * np.log10(np.maximum(power, 10 ** (FLOOR_DB / 10))))
    return np.concatenate(levels) if levels else np.array([FLOOR_DB])


def suggest_threshold(wav: Path) -> ThresholdSuggestion:
    """Suggest a silence threshold: noise floor (10th percentile) + 6 dB, within [-50, -20].

    Args:
        wav: 16-bit PCM WAV (see ``extract_track_wav``).

    Returns:
        The suggestion; ``warning`` is set when floor and voice level (90th percentile) are
        less than 10 dB apart, meaning there is no real silence on the track.

    Raises:
        ValueError: the WAV is not 16-bit PCM.
        OSError: the file cannot be opened.
        wave.Error: the file is not a PCM WAV.
    """
    db = window_rms_db(wav)
    floor, voice = (float(v) for v in np.percentile(db, [10, 90]))
    threshold = min(max(floor + MARGIN_DB, THRESHOLD_MIN_DB), THRESHOLD_MAX_DB)
    return ThresholdSuggestion(
        threshold_db=round(threshold, 1),
        floor_db=round(floor, 1),
        voice_db=round(voice, 1),
        warning=NOISY_WARNING if voice - floor < MIN_GAP_DB else None,
    )
