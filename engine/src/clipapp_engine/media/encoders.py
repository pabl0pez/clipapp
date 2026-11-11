"""H.264 encoder selection backed by a cache of doctor's real encode probes.

The cache lives at ``<data>/encoders.json`` and is keyed by the FFmpeg version, so installing a
different FFmpeg build triggers a fresh probe.
"""

import json
from collections.abc import Iterable

from clipapp_engine import config
from clipapp_engine.doctor import EncoderResult, probe_encoder, tool_version

ENCODER_ORDER = ("h264_nvenc", "h264_qsv", "h264_amf", "libx264", "libopenh264")
CACHE_NAME = "encoders.json"

# Quality settings tuned to look roughly alike (~x264 CRF 20, visually clean gameplay):
# - nvenc: p5 ("slow") preset, VBR with cq 23 and no bitrate cap = constant quality.
# - qsv: ICQ mode with global_quality 23 (QSV only accepts nv12, which is 4:2:0 like yuv420p).
# - amf: constant QP; I/P/B 21/23/25 keeps keyframes sharper, like CRF does.
# - libx264: CRF 20, preset medium.
# - libopenh264: no constant-quality mode, so a bitrate that suits 1080p60 gameplay.
_QUALITY: dict[str, list[str]] = {
    "h264_nvenc": ["-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "23", "-b:v", "0"],
    "h264_qsv": ["-preset", "medium", "-global_quality", "23"],
    "h264_amf": ["-quality", "balanced", "-rc", "cqp", "-qp_i", "21", "-qp_p", "23", "-qp_b", "25"],
    "libx264": ["-preset", "medium", "-crf", "20"],
    "libopenh264": ["-b:v", "12M"],
}


class EncoderError(RuntimeError):
    """No usable encoder, or the requested one is unknown or unusable."""


def save_encoder_cache(ffmpeg_version: str, results: Iterable[EncoderResult]) -> None:
    """Store encode-probe results in ``<data>/encoders.json``.

    Args:
        ffmpeg_version: Version of the ffmpeg the probes ran with.
        results: Probe results (e.g. from ``run_doctor``).
    """
    data = {"ffmpeg_version": ffmpeg_version, "usable": {r.name: r.usable for r in results}}
    (config.data_dir() / CACHE_NAME).write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_encoder_results() -> dict[str, bool]:
    """Return ``{encoder: usable}``, probing and caching again if FFmpeg's version changed.

    Raises:
        ToolNotFoundError: ffmpeg cannot be found.
    """
    ffmpeg = config.ffmpeg_path()
    version = tool_version(ffmpeg)
    try:
        data = json.loads((config.data_dir() / CACHE_NAME).read_text(encoding="utf-8"))
        if data["ffmpeg_version"] == version and set(ENCODER_ORDER) <= data["usable"].keys():
            return {k: bool(v) for k, v in data["usable"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass  # missing or corrupt cache: probe again
    results = [probe_encoder(ffmpeg, name) for name in ENCODER_ORDER]
    save_encoder_cache(version, results)
    return {r.name: r.usable for r in results}


def pick_encoder(preference: str | None = None) -> str:
    """Choose the H.264 encoder to use.

    Args:
        preference: Encoder the user asked for; it must be usable.

    Returns:
        ``preference``, or the first usable of nvenc, qsv, amf, libx264, libopenh264.

    Raises:
        EncoderError: the preferred encoder is unknown/unusable, or none is usable.
    """
    usable = load_encoder_results()
    if preference is not None:
        if preference not in _QUALITY:
            raise EncoderError(f"Unknown encoder {preference}; choose one of {ENCODER_ORDER}")
        if not usable.get(preference):
            raise EncoderError(f"Encoder {preference} is not usable on this PC (see doctor)")
        return preference
    for name in ENCODER_ORDER:
        if usable.get(name):
            return name
    raise EncoderError("No usable H.264 encoder found (see clipapp-engine doctor)")


def video_encoder_args(name: str) -> list[str]:
    """Return ``-c:v`` plus quality and pixel-format arguments for an encoder in ENCODER_ORDER."""
    pix_fmt = "nv12" if name == "h264_qsv" else "yuv420p"
    return ["-c:v", name, *_QUALITY[name], "-pix_fmt", pix_fmt]
