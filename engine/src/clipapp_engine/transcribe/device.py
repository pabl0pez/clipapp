"""Choice of Whisper model, device and compute type for the hardware at hand."""

import ctypes
import importlib.util
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import ctranslate2

log = logging.getLogger(__name__)

GPU_MODEL = "large-v3-turbo"  # ~1.6 GB in int8_float16: fits a 4 GB GTX 1650 Ti
SMALL_MODEL = "small"  # fallback on CPU and on GPU out-of-memory
GPU_COMPUTE = "int8_float16"
CPU_COMPUTE = "int8"

# Overrides ("configuration"); empty or unset means auto-detect.
MODEL_ENV = "CLIPAPP_WHISPER_MODEL"
DEVICE_ENV = "CLIPAPP_WHISPER_DEVICE"
COMPUTE_ENV = "CLIPAPP_WHISPER_COMPUTE_TYPE"

# DLLs ctranslate2 4.x loads for Whisper on Windows (CUDA 12 cuBLAS, cuDNN 9).
_CUDA_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll")
_NVIDIA_PACKAGES = ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc")


@dataclass(frozen=True)
class DeviceChoice:
    """Model, device and compute type to load, plus why it is not the preferred setup.

    Attributes:
        model: faster-whisper model name (``large-v3-turbo``, ``small``...).
        device: ``cuda`` or ``cpu``.
        compute_type: ctranslate2 compute type.
        warnings: Human-readable degradation notices (empty when nothing degraded).
    """

    model: str
    device: str
    compute_type: str
    warnings: tuple[str, ...] = field(default=())


def register_cuda_dlls() -> list[Path]:
    """Make the pip-installed NVIDIA DLLs (``[cuda]`` extra) loadable on Windows.

    Adds each package's ``bin`` folder with ``os.add_dll_directory`` and to ``PATH``
    (cuDNN loads its sub-libraries with plain ``LoadLibrary``, which only searches PATH).

    Returns:
        The folders registered (empty off Windows or without the extra).
    """
    if sys.platform != "win32":
        return []
    found: list[Path] = []
    for name in _NVIDIA_PACKAGES:
        try:
            spec = importlib.util.find_spec(name)
        except ModuleNotFoundError:
            continue
        for location in (spec.submodule_search_locations or []) if spec else []:
            bin_dir = Path(location) / "bin"
            if bin_dir.is_dir():
                found.append(bin_dir)
    path = os.environ.get("PATH", "")
    for bin_dir in found:
        os.add_dll_directory(str(bin_dir))
        if str(bin_dir) not in path:
            path = f"{bin_dir}{os.pathsep}{path}"
    os.environ["PATH"] = path
    return found


def cuda_problem() -> str | None:
    """Explain why Whisper cannot run on CUDA, or return None when it can.

    Raises:
        Nothing: every failure becomes the returned reason.
    """
    register_cuda_dlls()
    try:
        count = int(ctranslate2.get_cuda_device_count())
    except Exception as exc:  # any CUDA init failure just means "use the CPU"
        return f"CUDA failed to initialize ({exc})"
    if count == 0:
        return "no NVIDIA GPU with CUDA was found"
    if sys.platform == "win32":
        for dll in _CUDA_DLLS:
            try:
                ctypes.WinDLL(dll)
            except OSError:
                return f"{dll} not found (install it with `uv sync --extra cuda`)"
    return None


def pick_device(
    model: str | None = None, device: str | None = None, compute_type: str | None = None
) -> DeviceChoice:
    """Pick model, device and compute type; explicit values beat env overrides beat detection.

    Auto mode: NVIDIA GPU usable -> cuda/int8_float16/large-v3-turbo; otherwise
    cpu/int8/small with a warning. The GPU out-of-memory fallback to ``small`` happens when
    the model runs (see ``whisper.transcribe_range``).

    Args:
        model: Force a model name.
        device: Force ``cuda`` or ``cpu`` (skips CUDA detection).
        compute_type: Force a ctranslate2 compute type.

    Returns:
        The DeviceChoice.
    """
    model = model or os.environ.get(MODEL_ENV) or None
    device = device or os.environ.get(DEVICE_ENV) or None
    compute_type = compute_type or os.environ.get(COMPUTE_ENV) or None
    warnings: tuple[str, ...] = ()
    if device is None:
        problem = cuda_problem()
        if problem is None:
            device = "cuda"
        else:
            device = "cpu"
            warnings = (f"Transcribing on CPU (slower): {problem}.",)
    elif device == "cuda":
        register_cuda_dlls()
    if model is None:
        model = GPU_MODEL if device == "cuda" else SMALL_MODEL
    if compute_type is None:
        compute_type = GPU_COMPUTE if device == "cuda" else CPU_COMPUTE
    for w in warnings:
        log.warning(w)
    return DeviceChoice(model, device, compute_type, warnings)


def is_out_of_memory(exc: BaseException) -> bool:
    """Return True when a ctranslate2 error is a CUDA out-of-memory failure."""
    return "out of memory" in str(exc).lower()
