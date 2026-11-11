"""Engine configuration: data directories and external tool resolution."""

import os
import shutil
from pathlib import Path

DATA_SUBDIRS = ("cache", "models", "logs")
FFMPEG_DIR_ENV = "CLIPAPP_FFMPEG_DIR"


class ToolNotFoundError(RuntimeError):
    """Raised when ffmpeg or ffprobe cannot be located."""


def data_dir() -> Path:
    """Return the engine data directory, creating it and its subfolders if needed.

    Returns:
        ``%APPDATA%/clipapp`` (falls back to ``~/AppData/Roaming/clipapp`` when APPDATA is unset),
        guaranteed to contain ``cache/``, ``models/`` and ``logs/``.
    """
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    root = base / "clipapp"
    for sub in DATA_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def _resolve_tool(name: str) -> Path:
    custom = os.environ.get(FFMPEG_DIR_ENV)
    if custom:
        for candidate in (Path(custom) / f"{name}.exe", Path(custom) / name):
            if candidate.is_file():
                return candidate
        raise ToolNotFoundError(f"{name} not found in {FFMPEG_DIR_ENV}={custom}")
    found = shutil.which(name)
    if found is None:
        raise ToolNotFoundError(f"{name} not found on PATH; install FFmpeg or set {FFMPEG_DIR_ENV}")
    return Path(found)


def ffmpeg_path() -> Path:
    """Return the ffmpeg executable path.

    Uses ``CLIPAPP_FFMPEG_DIR`` when set, otherwise searches PATH.

    Raises:
        ToolNotFoundError: if ffmpeg cannot be found.
    """
    return _resolve_tool("ffmpeg")


def ffprobe_path() -> Path:
    """Return the ffprobe executable path.

    Uses ``CLIPAPP_FFMPEG_DIR`` when set, otherwise searches PATH.

    Raises:
        ToolNotFoundError: if ffprobe cannot be found.
    """
    return _resolve_tool("ffprobe")
