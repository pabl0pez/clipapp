"""Build every UI-side file of a recording (WAV, waveform, proxy, thumbnails) with caching."""

import json
import threading
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from clipapp_engine.export.full import RenderOptions
from clipapp_engine.media.audio import extract_track_wav, track_wav_path
from clipapp_engine.media.errors import MediaNotFoundError
from clipapp_engine.media.proxy import build_proxy, proxy_path
from clipapp_engine.media.thumbnails import build_thumbnails, thumbnails_paths
from clipapp_engine.media.waveform import build_waveform, waveform_path

FORMAT_VERSION = 1  # bump when any cached file's format changes: old caches get rebuilt
MANIFEST_NAME = "manifest.json"
# Progress weights per step (sum 1); the proxy is a full re-encode, so it dominates.
WEIGHTS = {"wav": 0.10, "waveform": 0.05, "proxy": 0.75, "thumbnails": 0.10}

ProgressFn = Callable[[str, float], None]


class Manifest(BaseModel):
    """Index of the prepared files; paths are relative to the manifest's folder."""

    version: int
    source: str
    mic_track: int
    wav: str
    waveform: str
    proxy: str
    sprite: str
    thumbnails: str


def prepare(
    media: Path,
    mic_track: int,
    on_progress: ProgressFn | None = None,
    force: bool = False,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Build the analysis WAV, waveform, proxy and thumbnail sprite, skipping cached ones.

    A file is rebuilt when missing, when ``force`` is set, or when the existing manifest has
    another format version or mic track.

    Args:
        media: Source recording (never modified).
        mic_track: 0-based mic audio track (OBS track 2 is 1); the game track is the default 2.
        on_progress: Called with ``(step, overall progress in [0, 1])``; step is one of
            ``wav``, ``waveform``, ``proxy``, ``thumbnails``.
        force: Rebuild everything.
        cancel_event: Cancels the running step.

    Returns:
        Path of ``manifest.json``.

    Raises:
        MediaNotFoundError: the file does not exist.
        MediaError: the media is unreadable or a track does not exist.
        EncoderError: no usable encoder.
        FFmpegError: FFmpeg failed or was cancelled.
    """
    if not media.is_file():
        raise MediaNotFoundError(f"File not found: {media}")
    wav = track_wav_path(media, mic_track)
    folder = wav.parent
    manifest_file = folder / MANIFEST_NAME
    if not force:
        try:
            old = Manifest.model_validate_json(manifest_file.read_text(encoding="utf-8"))
            force = old.version != FORMAT_VERSION or old.mic_track != mic_track
        except (OSError, ValueError):
            pass  # no or unreadable manifest: keep whatever files exist
    sprite, index = thumbnails_paths(media)
    proxy, waveform = proxy_path(media), waveform_path(wav)
    if force:
        for path in (wav, waveform, proxy, sprite, index):
            path.unlink(missing_ok=True)

    done = 0.0

    def step(name: str) -> Callable[[float], None]:
        start = done
        return lambda p: on_progress(name, start + p * WEIGHTS[name]) if on_progress else None

    extract_track_wav(media, mic_track, step("wav"), cancel_event)
    done += WEIGHTS["wav"]
    if not waveform.is_file():
        build_waveform(wav)
    step("waveform")(1.0)
    done += WEIGHTS["waveform"]
    if not proxy.is_file():
        build_proxy(media, RenderOptions(mic_track=mic_track), step("proxy"), cancel_event)
    step("proxy")(1.0)
    done += WEIGHTS["proxy"]
    if not (sprite.is_file() and index.is_file()):
        build_thumbnails(media, step("thumbnails"), cancel_event)
    step("thumbnails")(1.0)

    manifest = Manifest(
        version=FORMAT_VERSION,
        source=str(media.resolve()),
        mic_track=mic_track,
        wav=wav.name,
        waveform=waveform.name,
        proxy=proxy.name,
        sprite=sprite.name,
        thumbnails=index.name,
    )
    manifest_file.write_text(json.dumps(manifest.model_dump(), indent=2), encoding="utf-8")
    return manifest_file
