"""Thumbnail sprite (one JPG grid) for the timeline scrubber."""

import json
import math
import threading
from collections.abc import Callable
from pathlib import Path

from clipapp_engine.config import data_dir
from clipapp_engine.media.hashing import file_hash
from clipapp_engine.media.probe import probe
from clipapp_engine.media.runner import run_ffmpeg

INTERVAL_S = 10
THUMB_WIDTH = 160
COLUMNS = 10


def thumbnails_paths(media: Path) -> tuple[Path, Path]:
    """Return ``(sprite.jpg, thumbnails.json)`` in ``<data>/cache/<file_hash>/``.

    Raises:
        OSError: the media file cannot be read.
    """
    folder = data_dir() / "cache" / file_hash(media)
    return folder / "sprite.jpg", folder / "thumbnails.json"


def build_thumbnails(
    media: Path,
    on_progress: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[Path, Path]:
    """Render a sprite with a 160 px wide thumbnail every 10 s in a 10-column grid.

    The JSON index holds ``interval_s``, ``width``, ``height`` (one thumbnail), ``columns``,
    ``rows`` and ``count``; thumbnail ``i`` (time ``i * interval_s``) sits at column
    ``i % columns``, row ``i // columns``.

    Args:
        media: Source recording (never modified).
        on_progress: Progress in [0, 1].
        cancel_event: Cancels the run.

    Returns:
        ``(sprite path, index path)``.

    Raises:
        MediaError: the media is unreadable.
        FFmpegError: FFmpeg failed or was cancelled.
    """
    info = probe(media)
    src_w, src_h = (
        (info.height, info.width) if info.rotation in (90, 270) else (info.width, info.height)
    )
    height = max(2, round(THUMB_WIDTH * src_h / src_w / 2) * 2)
    # 0.1 s slack: container duration often exceeds the last frame (audio padding).
    count = max(1, math.ceil((info.duration_s - 0.1) / INTERVAL_S))
    rows = math.ceil(count / COLUMNS)
    sprite, index = thumbnails_paths(media)
    sprite.parent.mkdir(parents=True, exist_ok=True)
    part = sprite.with_name("sprite.part.jpg")
    vf = (
        f"select='gte(t,selected_n*{INTERVAL_S})',scale={THUMB_WIDTH}:{height},"
        f"tile={COLUMNS}x{rows}"
    )
    # shortcut: keyframes only (fast on 2 h 4K), so a thumb lands on the first keyframe at or
    # after its time; decode every frame if exact thumbnails are ever needed.
    args = ["-skip_frame", "nokey", "-i", str(media), "-an", "-vf", vf, "-fps_mode", "passthrough"]
    args += ["-frames:v", "1", "-update", "1", "-q:v", "4", str(part)]
    try:
        run_ffmpeg(args, info.duration_s, on_progress, cancel_event)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    part.replace(sprite)
    doc = {
        "interval_s": INTERVAL_S,
        "width": THUMB_WIDTH,
        "height": height,
        "columns": COLUMNS,
        "rows": rows,
        "count": count,
    }
    index.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return sprite, index
