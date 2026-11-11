"""Media inspection with ffprobe."""

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from clipapp_engine.config import ffprobe_path
from clipapp_engine.media.errors import (
    MediaNotFoundError,
    NoAudioStreamError,
    NoVideoStreamError,
    ProbeError,
)
from clipapp_engine.media.runner import NO_WINDOW

PROBE_TIMEOUT_S = 60


class AudioTrack(BaseModel):
    """One audio stream of a media file.

    ``index`` is the position among audio streams (0-based, what ``-map 0:a:N`` uses);
    ``stream_index`` is the absolute stream index in the container.
    """

    index: int
    stream_index: int
    codec: str
    channels: int
    sample_rate: int
    title: str | None = None
    language: str | None = None


class MediaInfo(BaseModel):
    """Summary of a media file: first video stream plus every audio stream."""

    path: Path
    duration_s: float
    fps: Fraction
    is_vfr: bool
    width: int
    height: int
    video_codec: str
    pix_fmt: str | None
    rotation: int
    audio_tracks: list[AudioTrack]


def _rate(value: str | None) -> Fraction | None:
    """Parse an ffprobe rate like ``30000/1001``; ``0/0`` and garbage give None."""
    try:
        rate = Fraction(value or "")
    except (ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None


def _tag(tags: dict[str, str], key: str) -> str | None:
    """Return a tag case-insensitively (MKV muxers may write ``TITLE``)."""
    return next((v for k, v in tags.items() if k.lower() == key), None)


def _rotation(stream: dict[str, Any]) -> int:
    """Return clockwise display rotation normalized to 0/90/180/270."""
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            # Display Matrix rotation is counter-clockwise.
            return int(-round(float(side["rotation"]))) % 360
    rotate = _tag(stream.get("tags", {}), "rotate")
    return int(rotate) % 360 if rotate and rotate.lstrip("-").isdigit() else 0


def parse_probe(path: Path, data: dict[str, Any]) -> MediaInfo:
    """Build a MediaInfo from ffprobe JSON output.

    Args:
        path: The probed file.
        data: Parsed ``ffprobe -show_format -show_streams`` JSON.

    Returns:
        The MediaInfo.

    Raises:
        NoVideoStreamError: no video stream (cover-art attachments do not count).
        NoAudioStreamError: no audio stream.
        ProbeError: missing duration or frame rate.
    """
    streams: list[dict[str, Any]] = data.get("streams", [])
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        raise NoVideoStreamError(f"No video stream in {path}")
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio:
        raise NoAudioStreamError(f"No audio stream in {path}")

    avg, real = _rate(video.get("avg_frame_rate")), _rate(video.get("r_frame_rate"))
    fps = avg or real
    if fps is None:
        raise ProbeError(f"Cannot determine frame rate of {path}")
    duration = data.get("format", {}).get("duration") or video.get("duration")
    if duration is None:
        raise ProbeError(f"Cannot determine duration of {path}")

    return MediaInfo(
        path=path,
        duration_s=float(duration),
        fps=fps,
        is_vfr=avg is not None and real is not None and avg != real,
        width=int(video["width"]),
        height=int(video["height"]),
        video_codec=video.get("codec_name", "unknown"),
        pix_fmt=video.get("pix_fmt"),
        rotation=_rotation(video),
        audio_tracks=[
            AudioTrack(
                index=i,
                stream_index=int(s["index"]),
                codec=s.get("codec_name", "unknown"),
                channels=int(s.get("channels", 0)),
                sample_rate=int(s.get("sample_rate", 0)),
                title=_tag(s.get("tags", {}), "title"),
                language=_tag(s.get("tags", {}), "language"),
            )
            for i, s in enumerate(audio)
        ],
    )


def probe(path: Path) -> MediaInfo:
    """Inspect a media file with ffprobe.

    Args:
        path: Video file (MP4, MKV, MOV, ...).

    Returns:
        The MediaInfo of the file.

    Raises:
        MediaNotFoundError: the file does not exist.
        ProbeError: ffprobe failed (includes stderr tail) or output was unusable.
        NoVideoStreamError: the file has no video stream.
        NoAudioStreamError: the file has no audio stream.
    """
    if not path.is_file():
        raise MediaNotFoundError(f"File not found: {path}")
    args = [
        str(ffprobe_path()),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = subprocess.run(
            args,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_S,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(f"ffprobe could not run on {path}: {exc}") from exc
    tail = "\n".join(proc.stderr.strip().splitlines()[-40:])
    if proc.returncode != 0:
        raise ProbeError(f"ffprobe failed on {path} (exit {proc.returncode})", tail)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned invalid JSON for {path}", tail) from exc
    return parse_probe(path, data)
