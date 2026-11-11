"""Spanish transcription of one clip range with word timestamps (faster-whisper)."""

import logging
import wave
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
from faster_whisper import WhisperModel

from clipapp_engine.media.audio import SAMPLE_RATE, extract_track_wav
from clipapp_engine.media.hashing import file_hash
from clipapp_engine.transcribe.cache import (
    load_transcript,
    save_transcript,
    transcript_cache_path,
)
from clipapp_engine.transcribe.device import (
    SMALL_MODEL,
    DeviceChoice,
    is_out_of_memory,
    pick_device,
)
from clipapp_engine.transcribe.download import ensure_model
from clipapp_engine.transcribe.glossary import Glossary
from clipapp_engine.transcribe.transcript import Transcript, Word

log = logging.getLogger(__name__)

LANGUAGE = "es"
MARGIN_S = 1.0  # extra audio on each side so words at the edges are not cut

# Loaded models, reused across calls in the same process (loading takes seconds).
_MODELS: dict[tuple[str, str, str], Any] = {}


def _model(choice: DeviceChoice) -> Any:
    key = (choice.model, choice.device, choice.compute_type)
    if key not in _MODELS:
        path = ensure_model(choice.model)
        _MODELS[key] = WhisperModel(
            str(path), device=choice.device, compute_type=choice.compute_type
        )
    return _MODELS[key]


def _read_range(wav: Path, start_s: float, end_s: float) -> np.ndarray[Any, np.dtype[np.float32]]:
    """Return mono float32 samples of ``[start_s, end_s)`` from the 16 kHz cached WAV."""
    with wave.open(str(wav), "rb") as f:
        first = round(start_s * SAMPLE_RATE)
        f.setpos(min(first, f.getnframes()))
        raw = f.readframes(round(end_s * SAMPLE_RATE) - first)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _run(
    choice: DeviceChoice,
    audio: np.ndarray[Any, np.dtype[np.float32]],
    offset_s: float,
    glossary: Glossary,
) -> list[Word]:
    segments, _info = _model(choice).transcribe(
        audio,
        language=LANGUAGE,
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=False,
        initial_prompt=glossary.prompt(),
    )
    return [
        Word(
            text=w.word.strip(),
            start_s=offset_s + w.start,
            end_s=offset_s + w.end,
            probability=w.probability,
        )
        for seg in segments
        for w in seg.words or []
        if w.word.strip()
    ]


def transcribe_range(  # noqa: PLR0913, PLR0917
    media: Path,
    track_index: int,
    start_s: float,
    end_s: float,
    glossary: Glossary | None = None,
    choice: DeviceChoice | None = None,
    on_warning: Callable[[str], None] | None = None,
) -> Transcript:
    """Transcribe ``[start_s, end_s]`` of one audio track in Spanish, word by word.

    Audio is read from the cached track WAV with ``MARGIN_S`` extra on each side; word
    times are moved to the original video's clock and words whose midpoint falls outside
    the range are dropped (the kept ones are clamped to it). Results are cached on disk.

    Args:
        media: Source recording (never modified).
        track_index: 0-based audio track (OBS mic = 1).
        start_s: Range start in seconds on the original clock.
        end_s: Range end in seconds.
        glossary: Prompt terms and replacements; None means empty.
        choice: Model/device to use; None runs ``pick_device()``.
        on_warning: Receives degradation notices (CPU fallback, GPU out of memory).

    Returns:
        The Transcript (``model`` is the model actually used).

    Raises:
        ValueError: invalid range.
        MediaError: unreadable media or missing track.
        FFmpegError: track extraction failed.
        OSError: model download or cache failure.
        RuntimeError: ctranslate2 failed for a reason other than GPU memory.
    """
    if not 0 <= start_s < end_s:
        raise ValueError(f"Invalid range: start {start_s} s, end {end_s} s")
    glossary = glossary or Glossary()
    choice = choice or pick_device()
    warn = on_warning or (lambda _msg: None)
    for msg in choice.warnings:
        warn(msg)

    wav = extract_track_wav(media, track_index)
    cache = transcript_cache_path(
        file_hash(media), track_index, start_s, end_s, choice.model, glossary.digest()
    )
    cached = load_transcript(cache)
    if cached is not None:
        return cached

    offset = max(0.0, start_s - MARGIN_S)
    audio = _read_range(wav, offset, end_s + MARGIN_S)
    try:
        words = _run(choice, audio, offset, glossary)
    except RuntimeError as exc:
        if not (choice.device == "cuda" and choice.model != SMALL_MODEL and is_out_of_memory(exc)):
            raise
        _MODELS.clear()  # free the VRAM held by the big model before loading the small one
        msg = f"GPU out of memory with {choice.model}; retrying on GPU with {SMALL_MODEL}."
        log.warning(msg)
        warn(msg)
        choice = replace(choice, model=SMALL_MODEL)
        words = _run(choice, audio, offset, glossary)

    kept = [
        w.model_copy(update={"start_s": max(w.start_s, start_s), "end_s": min(w.end_s, end_s)})
        for w in words
        if start_s <= (w.start_s + w.end_s) / 2 <= end_s
    ]
    transcript = Transcript(model=choice.model, language=LANGUAGE, words=glossary.apply(kept))
    save_transcript(cache, transcript)
    return transcript
