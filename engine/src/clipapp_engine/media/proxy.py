"""Lightweight 720p proxy the UI plays instead of the original recording."""

import threading
from collections.abc import Callable
from pathlib import Path

from clipapp_engine.config import data_dir
from clipapp_engine.export.full import RenderOptions, audio_input
from clipapp_engine.media.encoders import pick_encoder, video_encoder_args
from clipapp_engine.media.errors import MediaError
from clipapp_engine.media.hashing import file_hash
from clipapp_engine.media.probe import probe
from clipapp_engine.media.runner import run_ffmpeg

PROXY_HEIGHT = 720
PROXY_FPS = 30
KEYFRAME_INTERVAL = 15  # frames: a keyframe every 0.5 s makes seeking instant


def proxy_path(media: Path) -> Path:
    """Return ``<data>/cache/<file_hash>/proxy.mp4``.

    Raises:
        OSError: the media file cannot be read.
    """
    return data_dir() / "cache" / file_hash(media) / "proxy.mp4"


def build_proxy(
    media: Path,
    options: RenderOptions | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Encode the 720p30 H.264/AAC proxy of ``media`` (always re-encodes).

    Args:
        media: Source recording (never modified).
        options: Mic/game tracks and volumes (same mix as the export) and encoder preference.
        on_progress: Progress in [0, 1].
        cancel_event: Cancels the encode; no partial proxy is left behind.

    Returns:
        Path of the proxy.

    Raises:
        MediaError: the media is unreadable or a track does not exist.
        EncoderError: no usable encoder.
        FFmpegError: FFmpeg failed or was cancelled.
    """
    options = options or RenderOptions()
    info = probe(media)
    for track in (options.mic_track, options.game_track):
        if track >= len(info.audio_tracks):
            raise MediaError(f"Audio track {track} not found in {media}")
    mic, game = info.audio_tracks[options.mic_track], info.audio_tracks[options.game_track]
    graph = ";".join(
        [
            f"[0:v:0]scale=-2:{PROXY_HEIGHT},fps={PROXY_FPS}[v]",
            audio_input(mic, "mic", options.mic_volume),
            audio_input(game, "game", options.game_volume),
            "[mic][game]amix=inputs=2:normalize=0:duration=longest[a]",
        ]
    )
    out = proxy_path(media)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name("proxy.mp4.part")
    # -hwaccel auto decodes 4K on the GPU when possible and falls back to software.
    args = ["-hwaccel", "auto", "-i", str(media), "-filter_complex", graph]
    args += ["-map", "[v]", "-map", "[a]", *video_encoder_args(pick_encoder(options.encoder))]
    args += ["-g", str(KEYFRAME_INTERVAL), "-keyint_min", str(KEYFRAME_INTERVAL)]
    args += ["-sc_threshold", "0", "-bf", "0"]
    args += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"]
    args += ["-movflags", "+faststart", "-f", "mp4", str(part)]
    try:
        run_ffmpeg(args, info.duration_s, on_progress, cancel_event)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    part.replace(out)
    return out
