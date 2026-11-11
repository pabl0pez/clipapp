"""Render a recording without its silences: cut, mix mic + game audio and encode to MP4."""

import logging
import re
import shutil
import tempfile
import threading
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

from pydantic import BaseModel, Field

from clipapp_engine import config
from clipapp_engine.doctor import tool_version
from clipapp_engine.media.encoders import pick_encoder, video_encoder_args
from clipapp_engine.media.errors import FFmpegCancelledError, MediaError
from clipapp_engine.media.probe import AudioTrack, MediaInfo, probe
from clipapp_engine.media.runner import run_ffmpeg
from clipapp_engine.silence.ranges import Range

log = logging.getLogger(__name__)

BATCH_SIZE = 100
FADE_S = 0.01
SEEK_MARGIN_S = 2.0
CONCAT_SHARE = 0.05  # progress share of the final concat step on the batch path
AAC_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000"]

FrameRange = tuple[int, int]


class RenderOptions(BaseModel):
    """Audio mix and encoder choice for a render.

    Attributes:
        mic_track: 0-based audio track of the microphone (OBS track 2).
        game_track: 0-based audio track of the game (OBS track 3).
        mic_volume: Linear gain of the microphone.
        game_volume: Linear gain of the game audio.
        encoder: Forced FFmpeg encoder name; None picks the best usable one.
    """

    mic_track: int = Field(default=1, ge=0)
    game_track: int = Field(default=2, ge=0)
    mic_volume: float = Field(default=1.0, ge=0)
    game_volume: float = Field(default=0.6, ge=0)
    encoder: str | None = None


def snap_ranges(ranges: list[Range], fps: Fraction) -> list[FrameRange]:
    """Round range bounds to whole frames so each cut has equal audio and video length.

    Returns:
        ``(first_frame, end_frame)`` pairs (end exclusive); empty ranges are dropped.
    """
    snapped = [(round(s * fps), round(e * fps)) for s, e in ranges]
    return [(a, b) for a, b in snapped if b > a]


def audio_input(track: AudioTrack, label: str, volume: float) -> str:
    """Return the filter chain that turns one track into 48 kHz stereo at the given gain."""
    # Mono mic → both channels at full level (the default upmix would lower it by 3 dB).
    upmix = "pan=stereo|c0=c0|c1=c0," if track.channels == 1 else ""
    return (
        f"[0:a:{track.index}]{upmix}aformat=sample_fmts=fltp:sample_rates=48000:"
        f"channel_layouts=stereo,volume={volume}[{label}]"
    )


def build_filtergraph(
    info: MediaInfo, frames: list[FrameRange], options: RenderOptions, offset_frames: int = 0
) -> str:
    """Build the trim/concat filtergraph producing ``[v]`` and ``[a]``.

    Args:
        info: Probe of the source.
        frames: Frame ranges to keep, from ``snap_ranges``.
        options: Track choice and volumes.
        offset_frames: Frame the input starts at (after an input seek).

    Returns:
        The filtergraph text (one statement per line).
    """
    fps, n = info.fps, len(frames)
    mic, game = info.audio_tracks[options.mic_track], info.audio_tracks[options.game_track]
    vfr = f"fps={fps}," if info.is_vfr else ""
    lines = [
        f"[0:v:0]{vfr}split={n}" + "".join(f"[v{i}]" for i in range(n)),
        audio_input(mic, "mic", options.mic_volume),
        audio_input(game, "game", options.game_volume),
        "[mic][game]amix=inputs=2:normalize=0:duration=longest,"
        f"asplit={n}" + "".join(f"[a{i}]" for i in range(n)),
    ]
    half = float(1 / fps) / 2
    for i, (a, b) in enumerate(frames):
        start, end = float((a - offset_frames) / fps), float((b - offset_frames) / fps)
        # Video bounds sit half a frame early so frame timestamps never land on a bound.
        lines.append(
            f"[v{i}]trim=start={max(start - half, 0):.6f}:end={end - half:.6f},"
            f"setpts=PTS-STARTPTS[vs{i}]"
        )
        lines.append(
            f"[a{i}]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={FADE_S},afade=t=out:st={end - start - FADE_S:.6f}:d={FADE_S}[as{i}]"
        )
    pairs = "".join(f"[vs{i}][as{i}]" for i in range(n))
    lines.append(f"{pairs}concat=n={n}:v=1:a=1[v][a]")
    return ";\n".join(lines)


def _filter_script_args(path: Path) -> list[str]:
    """Return the option that loads a filtergraph from a file for the installed FFmpeg.

    FFmpeg 7 replaced ``-filter_complex_script`` with the generic ``-/filter_complex`` form.
    """
    match = re.match(r"n?(\d+)\.", tool_version(config.ffmpeg_path()))
    # Git builds ("N-12345-g...") have no number; they are newer than 7.
    if match and int(match.group(1)) < 7:
        return ["-filter_complex_script", str(path)]
    return ["-/filter_complex", str(path)]


def _render_part(  # noqa: PLR0913, PLR0917
    info: MediaInfo,
    frames: list[FrameRange],
    options: RenderOptions,
    encoder: str,
    workdir: Path,
    out: Path,
    audio_args: list[str],
    on_progress: Callable[[float], None] | None,
    cancel_event: threading.Event | None,
) -> None:
    """Encode one group of ranges to ``out`` with an input seek just before the first range."""
    fps = info.fps
    offset = max(0, frames[0][0] - round(SEEK_MARGIN_S * fps))
    script = workdir / f"{out.stem}.filtergraph"
    script.write_text(build_filtergraph(info, frames, options, offset), encoding="utf-8")
    args = ["-ss", f"{float(offset / fps):.6f}", "-i", str(info.path)]
    args += _filter_script_args(script)
    args += ["-map", "[v]", "-map", "[a]", *video_encoder_args(encoder), "-r", str(fps)]
    args += [*audio_args, "-movflags", "+faststart", str(out)]
    log.info("ffmpeg %s", args)
    run_ffmpeg(args, float(sum(b - a for a, b in frames) / fps), on_progress, cancel_event)


def _scaled(
    on_progress: Callable[[float], None] | None, start: float, share: float
) -> Callable[[float], None] | None:
    """Map a step's 0..1 progress onto ``[start, start + share]`` of the total."""
    if on_progress is None:
        return None
    return lambda p: on_progress(start + p * share)


def render_without_silences(  # noqa: PLR0913, PLR0917
    media: Path,
    keep_ranges: list[Range],
    options: RenderOptions,
    out: Path,
    on_progress: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
    batch_size: int = BATCH_SIZE,
) -> str:
    """Render ``media`` keeping only ``keep_ranges`` into an H.264/AAC MP4.

    Up to ``batch_size`` ranges are rendered in one FFmpeg run. More ranges are rendered in
    batches to intermediate MOV files (same video settings, lossless PCM audio, so no AAC
    priming gaps at the joins) that are joined with the concat demuxer, copying the video.
    Temporary files live next to ``out`` and are removed whatever happens; ``out`` only
    appears once the render succeeded.

    Args:
        media: Source recording (never modified).
        keep_ranges: Sorted ``(start_s, end_s)`` ranges, e.g. from ``compute_keep_ranges``.
        options: Tracks, volumes and encoder preference.
        out: Output ``.mp4`` path.
        on_progress: Overall progress in [0, 1].
        cancel_event: Cancels the render.
        batch_size: Ranges per FFmpeg run.

    Returns:
        Name of the video encoder used.

    Raises:
        ValueError: no range is at least one frame long.
        MediaError: the media is unreadable or a track does not exist.
        EncoderError: no usable encoder.
        FFmpegError: FFmpeg failed or the render was cancelled (FFmpegCancelledError).
    """
    info = probe(media)
    for track in (options.mic_track, options.game_track):
        if track >= len(info.audio_tracks):
            raise MediaError(f"Audio track {track} not found in {media}")
    frames = snap_ranges(keep_ranges, info.fps)
    if not frames:
        raise ValueError("Nothing to render: no keep range is at least one frame long")
    encoder = pick_encoder(options.encoder)
    batches = [frames[i : i + batch_size] for i in range(0, len(frames), batch_size)]
    log.info("render %s -> %s: encoder %s, %d ranges", media, out, encoder, len(frames))

    out.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=".clipapp-render-", dir=out.parent))
    try:
        final = workdir / "out.mp4"
        if len(batches) == 1:
            _render_part(
                info, frames, options, encoder, workdir, final, AAC_ARGS, on_progress, cancel_event
            )
        else:
            total = sum(b - a for a, b in frames)
            done, parts = 0, []
            for i, batch in enumerate(batches):
                if cancel_event is not None and cancel_event.is_set():
                    raise FFmpegCancelledError("render was cancelled")
                part = workdir / f"part{i:04d}.mov"
                length = sum(b - a for a, b in batch)
                progress = _scaled(
                    on_progress,
                    (1 - CONCAT_SHARE) * done / total,
                    (1 - CONCAT_SHARE) * length / total,
                )
                pcm = ["-c:a", "pcm_f32le", "-ac", "2", "-ar", "48000"]
                _render_part(
                    info, batch, options, encoder, workdir, part, pcm, progress, cancel_event
                )
                parts.append(part.name)
                done += length
            listing = workdir / "parts.txt"
            listing.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
            args = ["-f", "concat", "-i", str(listing), "-map", "0:v", "-map", "0:a"]
            args += ["-c:v", "copy", *AAC_ARGS, "-movflags", "+faststart", str(final)]
            run_ffmpeg(
                args,
                float(total / info.fps),
                _scaled(on_progress, 1 - CONCAT_SHARE, CONCAT_SHARE),
                cancel_event,
            )
        final.replace(out)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return encoder
