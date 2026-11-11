from pathlib import Path

import pytest

from clipapp_engine import config


def test_data_dir_creates_subfolders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "Datos ñandú"))
    root = config.data_dir()
    assert root == tmp_path / "Datos ñandú" / "clipapp"
    for sub in ("cache", "models", "logs"):
        assert (root / sub).is_dir()
    assert config.data_dir() == root  # idempotent


def test_data_dir_without_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert config.data_dir() == tmp_path / "AppData" / "Roaming" / "clipapp"


def test_tools_from_env_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools = tmp_path / "mi ffmpeg"
    tools.mkdir()
    (tools / "ffmpeg.exe").touch()
    (tools / "ffprobe.exe").touch()
    monkeypatch.setenv("CLIPAPP_FFMPEG_DIR", str(tools))
    assert config.ffmpeg_path() == tools / "ffmpeg.exe"
    assert config.ffprobe_path() == tools / "ffprobe.exe"


def test_env_dir_missing_tool_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLIPAPP_FFMPEG_DIR", str(tmp_path))
    with pytest.raises(config.ToolNotFoundError):
        config.ffmpeg_path()


def test_tools_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLIPAPP_FFMPEG_DIR", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: f"C:/bin/{name}.exe")
    assert config.ffprobe_path() == Path("C:/bin/ffprobe.exe")


def test_tool_missing_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLIPAPP_FFMPEG_DIR", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(config.ToolNotFoundError):
        config.ffmpeg_path()
