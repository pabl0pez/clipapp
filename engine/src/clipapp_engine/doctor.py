"""Environment diagnostics: FFmpeg tools, usable encoders, GPUs, CUDA and disk space."""

import importlib
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from clipapp_engine import config

ENCODERS = ("h264_nvenc", "hevc_nvenc", "h264_qsv", "h264_amf", "libx264", "libopenh264")
PROBE_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class EncoderResult:
    """Outcome of a real encode attempt with one encoder."""

    name: str
    usable: bool
    error: str | None = None


@dataclass(frozen=True)
class DoctorReport:
    """Full diagnostics report."""

    ffmpeg_version: str
    ffprobe_version: str
    gpus: list[str]
    encoders: list[EncoderResult]
    cuda: str
    data_dir: str
    disk_free_bytes: int

    def to_dict(self) -> dict[str, Any]:
        """Return the report as JSON-serializable primitives."""
        return asdict(self)


def tool_version(path: Path) -> str:
    """Return the version string reported by ``<tool> -version``.

    Args:
        path: ffmpeg or ffprobe executable.

    Returns:
        The version token (e.g. ``9.0.2-full_build-www.gyan.dev``), or ``"unknown"`` if it
        cannot be parsed or the tool fails to run.
    """
    try:
        proc = subprocess.run(
            [str(path), "-version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    words = proc.stdout.split()
    # First line looks like: "ffmpeg version <version> Copyright ..."
    return words[2] if len(words) > 2 and words[1] == "version" else "unknown"


def probe_encoder(ffmpeg: Path, name: str) -> EncoderResult:
    """Check whether an encoder really works by encoding 1 s of synthetic video.

    Listing in ``ffmpeg -encoders`` is not enough: hardware encoders appear even without the
    matching GPU/driver, so a real encode to the null muxer is attempted.

    Args:
        ffmpeg: ffmpeg executable.
        name: FFmpeg encoder name, e.g. ``h264_nvenc``.

    Returns:
        An EncoderResult; ``error`` holds the first stderr line (root cause) on failure.
    """
    args = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=640x360:rate=30:duration=1",
        "-c:v",
        name,
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(
            args, check=False, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return EncoderResult(name, False, "timeout")
    except OSError as exc:
        return EncoderResult(name, False, str(exc))
    if proc.returncode == 0:
        return EncoderResult(name, True)
    lines = proc.stderr.strip().splitlines()
    return EncoderResult(name, False, lines[0].strip() if lines else f"exit code {proc.returncode}")


def cuda_status() -> str:
    """Report whether CUDA is available to ctranslate2, without requiring it to be installed.

    Returns:
        ``"not installed"``, ``"no CUDA devices"``, ``"available (N device(s))"`` or an error text.
    """
    try:
        ct2 = importlib.import_module("ctranslate2")
    except ImportError:
        return "not installed"
    try:
        count = int(ct2.get_cuda_device_count())
    except Exception as exc:  # any failure here just means "no CUDA"
        return f"error: {exc}"
    return f"available ({count} device(s))" if count else "no CUDA devices"


def gpu_names() -> list[str]:
    """Return the names of the video adapters reported by Windows (empty list on failure)."""
    powershell = shutil.which("powershell")
    if powershell is None:
        return []
    try:
        proc = subprocess.run(
            [powershell, "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"],
            check=False,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def run_doctor() -> DoctorReport:
    """Run all diagnostics and return the report. Never raises for missing tools."""
    data = config.data_dir()
    try:
        ffmpeg = config.ffmpeg_path()
    except config.ToolNotFoundError as exc:
        ffmpeg_version = "not found"
        encoders = [EncoderResult(name, False, str(exc)) for name in ENCODERS]
    else:
        ffmpeg_version = tool_version(ffmpeg)
        encoders = [probe_encoder(ffmpeg, name) for name in ENCODERS]
    try:
        ffprobe_version = tool_version(config.ffprobe_path())
    except config.ToolNotFoundError:
        ffprobe_version = "not found"
    return DoctorReport(
        ffmpeg_version=ffmpeg_version,
        ffprobe_version=ffprobe_version,
        gpus=gpu_names(),
        encoders=encoders,
        cuda=cuda_status(),
        data_dir=str(data),
        disk_free_bytes=shutil.disk_usage(data).free,
    )
