"""Render one clip: exact cut, 16:9 or 9:16 framing, burned subtitles and mixed audio."""

import logging
import shutil
import tempfile
import threading
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Literal

from pydantic import Field

from clipapp_engine.export.full import AAC_ARGS, RenderOptions, audio_input
from clipapp_engine.media.encoders import pick_encoder, video_encoder_args
from clipapp_engine.media.errors import MediaError
from clipapp_engine.media.probe import MediaInfo, probe
from clipapp_engine.media.runner import run_ffmpeg
from clipapp_engine.subtitles.ass import FONTS_DIR, build_ass
from clipapp_engine.subtitles.style import SubtitleStyle
from clipapp_engine.transcribe.transcript import Transcript

log = logging.getLogger(__name__)

MIN_CLIP_S = 15.0
MAX_CLIP_S = 60.0
MAX_FPS = Fraction(60)
VERTICAL = (1080, 1920)
HORIZONTAL = (1920, 1080)
BLUR_SIZE = (270, 480)  # the background is blurred at quarter size: same look, much faster
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"

Aspect = Literal["16:9", "9:16"]


class ClipOptions(RenderOptions):
    """Everything that defines one exported clip (tracks, volumes and encoder are inherited).

    Attributes:
        start_s: Clip start on the original clock.
        end_s: Clip end on the original clock.
        aspect: ``16:9`` (up to 1920x1080) or ``9:16`` (1080x1920).
        crop_mode: 9:16 only. ``crop`` cuts a full-height 9:16 window; ``blur`` shows the
            whole frame over a blurred copy that fills the screen.
        crop_x: 9:16 crop only. Window position, 0 = left edge, 0.5 = centre, 1 = right edge.
        style: Subtitle look; None exports without subtitles.
        normalize: Apply loudnorm to -14 LUFS, -1.5 dBTP.
    """

    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    aspect: Aspect = "9:16"
    crop_mode: Literal["crop", "blur"] = "crop"
    crop_x: float = Field(default=0.5, ge=0, le=1)
    style: SubtitleStyle | None = None
    normalize: bool = True


def validate_range(start_s: float, end_s: float, duration_s: float | None = None) -> None:
    """Check that a clip lasts 15-60 s and fits inside the video.

    Raises:
        ValueError: with a message suitable for the user.
    """
    length = end_s - start_s
    if start_s < 0 or length <= 0:
        raise ValueError(f"end ({end_s:g} s) must be after start ({start_s:g} s)")
    if not MIN_CLIP_S <= length <= MAX_CLIP_S:
        raise ValueError(f"clip lasts {length:g} s; it must last {MIN_CLIP_S:g}-{MAX_CLIP_S:g} s")
    if duration_s is not None and end_s > duration_s:
        raise ValueError(f"end ({end_s:g} s) is past the end of the video ({duration_s:.2f} s)")


def _even(value: float) -> int:
    return max(2, round(value / 2) * 2)


def crop_window(width: int, height: int, crop_x: float) -> tuple[int, int, int, int]:
    """Return the ``(w, h, x, y)`` 9:16 window of a ``width`` x ``height`` frame.

    The window uses the full height (or the full width for frames narrower than 9:16) and is
    placed horizontally by ``crop_x``; it always lies inside the frame.
    """
    w = min(_even(height * 9 / 16), width - width % 2)
    h = min(_even(w * 16 / 9), height - height % 2)
    return w, h, round((width - w) * crop_x), (height - h) // 2


def output_size(info: MediaInfo, aspect: Aspect) -> tuple[int, int]:
    """Return the output resolution: 1080x1920, or the source fitted inside 1920x1080."""
    if aspect == "9:16":
        return VERTICAL
    scale = min(HORIZONTAL[0] / info.width, HORIZONTAL[1] / info.height, 1)
    if scale == 1:
        return info.width, info.height
    return _even(info.width * scale), _even(info.height * scale)


def output_fps(info: MediaInfo) -> Fraction:
    """Return the source frame rate capped at 60 fps."""
    return min(info.fps, MAX_FPS)


def video_filter(info: MediaInfo, options: ClipOptions, ass_name: str | None = None) -> str:
    """Build the video part of the filtergraph, ending in ``[v]``.

    Args:
        info: Probe of the source.
        options: Aspect, crop mode and position.
        ass_name: Subtitle file name relative to FFmpeg's working directory (fonts are in
            ``fonts/`` next to it); None burns nothing.
    """
    fps = output_fps(info)
    head = f"[0:v:0]fps={fps}," if info.is_vfr or info.fps != fps else "[0:v:0]"
    width, height = output_size(info, options.aspect)
    if options.aspect == "16:9":
        scale = "" if (width, height) == (info.width, info.height) else f"scale={width}:{height},"
        graph = f"{head}{scale}setsar=1"
    elif options.crop_mode == "crop":
        w, h, x, y = crop_window(info.width, info.height, options.crop_x)
        graph = f"{head}crop={w}:{h}:{x}:{y},scale={width}:{height},setsar=1"
    else:
        bw, bh = BLUR_SIZE
        graph = (
            f"{head}split[bg][fg];"
            f"[bg]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"boxblur=10:2,scale={width}:{height},setsar=1[blur];"
            f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease,setsar=1[front];"
            "[blur][front]overlay=(W-w)/2:(H-h)/2"
        )
    if ass_name is not None:
        graph += f",subtitles=filename={ass_name}:fontsdir=fonts"
    return graph + "[v]"


def audio_filter(info: MediaInfo, options: ClipOptions) -> str:
    """Build the audio part of the filtergraph: mic + game mix (and loudnorm), ending in ``[a]``."""
    mic, game = info.audio_tracks[options.mic_track], info.audio_tracks[options.game_track]
    norm = f",{LOUDNORM}" if options.normalize else ""
    return ";".join(
        [
            audio_input(mic, "mic", options.mic_volume),
            audio_input(game, "game", options.game_volume),
            f"[mic][game]amix=inputs=2:normalize=0:duration=longest{norm}[a]",
        ]
    )


def render_clip(  # noqa: PLR0913, PLR0917
    media: Path,
    options: ClipOptions,
    out: Path,
    transcript: Transcript | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> str:
    """Render one clip of ``media`` into an H.264/AAC MP4 (``+faststart``).

    The cut is frame-exact (input seek plus full re-encode). FFmpeg runs inside a temporary
    folder next to ``out`` holding the ``.ass`` file and a copy of the bundled fonts, so the
    subtitles filter only sees simple relative names (no Windows path escaping). ``out``
    appears only once the render succeeded; temporary files are always removed.

    Args:
        media: Source recording (never modified).
        options: Clip range, framing, subtitle style, audio and encoder.
        out: Output ``.mp4`` path.
        transcript: Words on the original clock; subtitles are burned when it and
            ``options.style`` are given.
        on_progress: Progress in [0, 1].
        cancel_event: Cancels the render.

    Returns:
        Name of the video encoder used.

    Raises:
        ValueError: the range is shorter than 15 s, longer than 60 s or outside the video.
        MediaError: the media is unreadable or a track does not exist.
        EncoderError: no usable encoder.
        FFmpegError: FFmpeg failed or the render was cancelled (FFmpegCancelledError).
    """
    info = probe(media)
    validate_range(options.start_s, options.end_s, info.duration_s)
    for track in (options.mic_track, options.game_track):
        if track >= len(info.audio_tracks):
            raise MediaError(f"Audio track {track} not found in {media}")
    encoder = pick_encoder(options.encoder)
    length = options.end_s - options.start_s
    width, height = output_size(info, options.aspect)

    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=".clipapp-clip-", dir=out.parent))
    try:
        ass_name = None
        if options.style is not None and transcript is not None:
            ass_name = "clip.ass"
            subs = build_ass(transcript, options.style, width, height, options.start_s)
            subs.save(str(workdir / ass_name))
            shutil.copytree(FONTS_DIR, workdir / "fonts")
        graph = f"{video_filter(info, options, ass_name)};{audio_filter(info, options)}"
        args = ["-ss", f"{options.start_s:.6f}", "-i", str(media.resolve())]
        args += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", f"{length:.6f}"]
        args += [*video_encoder_args(encoder), "-r", str(output_fps(info)), *AAC_ARGS]
        args += ["-movflags", "+faststart", "out.mp4"]
        log.info("clip %s -> %s: %s", media, out, args)
        run_ffmpeg(args, length, on_progress, cancel_event, cwd=workdir)
        (workdir / "out.mp4").replace(out)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return encoder
