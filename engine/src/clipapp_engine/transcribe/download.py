"""Whisper model files in ``<data>/models``: download once, then fully offline."""

from pathlib import Path

from faster_whisper import download_model

from clipapp_engine.config import data_dir


def model_dir(name: str) -> Path:
    """Return the local folder of a model (may not exist yet)."""
    return data_dir() / "models" / name


def ensure_model(name: str, force: bool = False) -> Path:
    """Return the model folder, downloading it from Hugging Face first if it is missing.

    Download progress is shown by huggingface_hub's progress bars on stderr.

    Args:
        name: faster-whisper model name (``small``, ``large-v3-turbo``...) or a HF repo id.
        force: Download again even if the files exist (resumes/repairs a broken copy).

    Returns:
        Folder containing ``model.bin``.

    Raises:
        ValueError: unknown model name.
        OSError: network or disk failure during the download.
    """
    folder = model_dir(name.replace("/", "--"))
    if force or not (folder / "model.bin").is_file():
        download_model(name, output_dir=str(folder))
    return folder
