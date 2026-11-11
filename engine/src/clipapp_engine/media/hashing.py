"""Cheap file fingerprint used as a cache key."""

import hashlib
from pathlib import Path

HEAD_BYTES = 4 * 1024 * 1024


def file_hash(path: Path) -> str:
    """Fingerprint a file without reading it whole.

    Args:
        path: File to fingerprint (may be tens of GB).

    Returns:
        ``"<size>-<mtime_ns>-<sha1 of first 4 MiB>"``.

    Raises:
        OSError: the file cannot be stat'ed or read.
    """
    stat = path.stat()
    with path.open("rb") as f:
        digest = hashlib.sha1(f.read(HEAD_BYTES), usedforsecurity=False).hexdigest()
    return f"{stat.st_size}-{stat.st_mtime_ns}-{digest}"
