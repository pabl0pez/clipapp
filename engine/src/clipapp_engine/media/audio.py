"""Extraction of a single audio track to a cached analysis WAV."""

import threading
from collections.abc import Callable
from pathlib import Path

from clipapp_engine.config import data_dir
from clipapp_engine.media.errors import MediaError, MediaNotFoundError
from clipapp_engine.media.hashing import file_hash
from clipapp_engine.media.probe import probe
from clipapp_engine.media.runner import run_ffmpeg

SAMPLE_RATE = 16_000


def track_wav_path(media: Path, track_index: int) -> Path:
    """Return the cache path of a track's WAV: ``<data>/cache/<file_hash>/track<N>.wav``.

    Raises:
        OSError: the media file cannot be read.
    """
    return data_dir() / "cache" / file_hash(media) / f"track{track_index}.wav"


def extract_track_wav(
    media: Path,
    track_index: int,
    on_progress: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Extract one audio track to a mono 16 kHz ``pcm_s16le`` WAV, reusing the cached copy.

    The WAV feeds silence detection, the waveform and transcription.

    Args:
        media: Source recording (never modified).
        track_index: 0-based audio-stream index (``-map 0:a:N``); OBS track 2 (mic) is 1.
        on_progress: Progress in [0, 1]; gets 1.0 immediately on a cache hit.
        cancel_event: Cancels the extraction; no partial WAV is left in the cache.

    Returns:
        Path of the cached WAV.

    Raises:
        MediaError: the file is missing/unreadable or has no such track (subclasses from probe).
        FFmpegError: extraction failed or was cancelled.
    """
    if not media.is_file():
        raise MediaNotFoundError(f"File not found: {media}")
    out = track_wav_path(media, track_index)
    if not out.is_file():
        info = probe(media)
        if not 0 <= track_index < len(info.audio_tracks):
            raise MediaError(
                f"Audio track {track_index} not found in {media} "
                f"(has {len(info.audio_tracks)}: 0..{len(info.audio_tracks) - 1})"
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        part = out.with_suffix(".wav.part")
        args = ["-i", str(media), "-map", f"0:a:{track_index}", "-vn", "-ac", "1"]
        args += ["-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", "-f", "wav", str(part)]
        try:
            run_ffmpeg(args, info.duration_s, on_progress, cancel_event)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        part.replace(out)
    elif on_progress is not None:
        on_progress(1.0)
    return out
