"""Precomputed min/max peaks of a track WAV for wavesurfer.js.

The JSON follows the BBC ``audiowaveform`` format (version 2, 8 bits, one channel):
``data`` is ``[min0, max0, min1, max1, ...]`` in -128..127, ``samples_per_pixel`` source samples
per pair. Extra keys ``duration`` and ``peaks_per_second`` save the UI from recomputing them.
In wavesurfer v7: ``ws.load(url, [data.map(v => v / 128)], duration)``.
"""

import json
import wave
from pathlib import Path

import numpy as np

PEAKS_PER_SECOND = 50
BLOCK_PEAKS = 10_000  # peaks per read: 3.2 M samples (6.4 MB) at 16 kHz


def waveform_path(wav: Path) -> Path:
    """Return the waveform JSON path next to the WAV (``cache/<file_hash>/waveform.json``)."""
    return wav.with_name("waveform.json")


def build_waveform(wav: Path) -> Path:
    """Compute 50 min/max pairs per second from a mono 16-bit WAV, reading it in blocks.

    Args:
        wav: WAV from ``extract_track_wav`` (mono ``pcm_s16le``).

    Returns:
        Path of ``waveform.json``.

    Raises:
        OSError: the WAV cannot be read or the JSON written.
        wave.Error: the file is not a valid WAV.
        ValueError: the WAV is not mono 16-bit or its rate is not a multiple of 50 Hz.
    """
    with wave.open(str(wav), "rb") as f:
        rate, frames = f.getframerate(), f.getnframes()
        if f.getnchannels() != 1 or f.getsampwidth() != 2 or rate % PEAKS_PER_SECOND:
            raise ValueError(f"{wav} must be mono 16-bit with a rate multiple of 50 Hz")
        spp = rate // PEAKS_PER_SECOND
        chunks: list[np.ndarray] = []
        while block := f.readframes(spp * BLOCK_PEAKS):
            samples = np.frombuffer(block, dtype="<i2")
            pad = -len(samples) % spp
            # Edge padding repeats the last sample, so the final partial bucket keeps its peaks.
            buckets = np.pad(samples, (0, pad), mode="edge").reshape(-1, spp)
            pairs = np.stack([buckets.min(axis=1), buckets.max(axis=1)], axis=1)
            chunks.append((pairs >> 8).astype(np.int8).ravel())
    data = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int8)
    doc = {
        "version": 2,
        "channels": 1,
        "sample_rate": rate,
        "samples_per_pixel": spp,
        "bits": 8,
        "length": len(data) // 2,
        "duration": frames / rate,
        "peaks_per_second": PEAKS_PER_SECOND,
        "data": data.tolist(),
    }
    out = waveform_path(wav)
    out.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    return out
