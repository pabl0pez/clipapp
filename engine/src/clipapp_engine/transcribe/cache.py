"""On-disk cache of transcripts keyed by media, track, range, model and glossary."""

import hashlib
from pathlib import Path

from pydantic import ValidationError

from clipapp_engine.config import data_dir
from clipapp_engine.transcribe.transcript import Transcript


def transcript_cache_path(  # noqa: PLR0913, PLR0917
    media_hash: str, track_index: int, start_s: float, end_s: float, model: str, glossary: str
) -> Path:
    """Return the cache file for one transcription request.

    Args:
        media_hash: ``file_hash`` of the recording.
        track_index: 0-based audio track.
        start_s: Range start in seconds.
        end_s: Range end in seconds.
        model: Whisper model name.
        glossary: ``Glossary.digest()``.

    Returns:
        ``<data>/cache/<media_hash>/transcripts/<key>.json`` (may not exist).
    """
    raw = f"{track_index}|{start_s:.3f}|{end_s:.3f}|{model}|{glossary}"
    key = hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()
    return data_dir() / "cache" / media_hash / "transcripts" / f"{key}.json"


def load_transcript(path: Path) -> Transcript | None:
    """Return the cached transcript, or None when missing or unreadable."""
    try:
        return Transcript.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def save_transcript(path: Path, transcript: Transcript) -> None:
    """Write a transcript atomically (a crash never leaves a half-written cache entry).

    Raises:
        OSError: the cache folder cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".json.part")
    part.write_text(transcript.model_dump_json(), encoding="utf-8")
    part.replace(path)
