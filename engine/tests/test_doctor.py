import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from clipapp_engine import doctor
from clipapp_engine.cli import app

FFMPEG = Path("C:/bin/ffmpeg.exe")


def _done(returncode: int = 0, stdout: str = "", stderr: str = "") -> Any:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_tool_version_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    out = "ffmpeg version 9.0.2-full_build-www.gyan.dev Copyright (c) 2000-2026\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _done(stdout=out))
    assert doctor.tool_version(FFMPEG) == "9.0.2-full_build-www.gyan.dev"


def test_tool_version_unparseable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _done(stdout="garbage"))
    assert doctor.tool_version(FFMPEG) == "unknown"


def test_probe_encoder_usable_uses_arg_list(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        calls.append(args)
        return _done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert doctor.probe_encoder(FFMPEG, "libx264") == doctor.EncoderResult("libx264", True)
    args = calls[0]
    assert args[0] == str(FFMPEG)
    assert args[args.index("-c:v") + 1] == "libx264"
    assert "lavfi" in args


def test_probe_encoder_failure_reports_first_stderr_line(monkeypatch: pytest.MonkeyPatch) -> None:
    err = "[h264_nvenc] Cannot load nvcuda.dll\nError while opening encoder\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _done(1, stderr=err))
    result = doctor.probe_encoder(FFMPEG, "h264_nvenc")
    assert not result.usable
    assert result.error == "[h264_nvenc] Cannot load nvcuda.dll"


def test_probe_encoder_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a: Any, **k: Any) -> Any:
        raise subprocess.TimeoutExpired("ffmpeg", 1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert doctor.probe_encoder(FFMPEG, "h264_qsv") == doctor.EncoderResult(
        "h264_qsv", False, "timeout"
    )


def test_cuda_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "ctranslate2", None)  # forces ImportError
    assert doctor.cuda_status() == "not installed"


def test_cuda_available(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.SimpleNamespace(get_cuda_device_count=lambda: 1)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    assert doctor.cuda_status() == "available (1 device(s))"


def test_cuda_zero_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.SimpleNamespace(get_cuda_device_count=lambda: 0)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    assert doctor.cuda_status() == "no CUDA devices"


def test_gpu_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "powershell.exe")
    out = "NVIDIA GeForce GTX 1650 Ti\r\nIntel(R) UHD Graphics\r\n\r\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _done(stdout=out))
    assert doctor.gpu_names() == ["NVIDIA GeForce GTX 1650 Ti", "Intel(R) UHD Graphics"]


def test_run_doctor_without_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("CLIPAPP_FFMPEG_DIR", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    report = doctor.run_doctor()
    assert report.ffmpeg_version == "not found"
    assert report.ffprobe_version == "not found"
    assert [e.name for e in report.encoders] == list(doctor.ENCODERS)
    assert not any(e.usable for e in report.encoders)
    assert report.disk_free_bytes > 0


def _fake_report() -> doctor.DoctorReport:
    return doctor.DoctorReport(
        ffmpeg_version="9.0",
        ffprobe_version="9.0",
        gpus=["AMD Radeon"],
        encoders=[doctor.EncoderResult("h264_amf", True), doctor.EncoderResult("x", False, "e")],
        cuda="not installed",
        data_dir="C:/data",
        disk_free_bytes=2 * 1024**3,
    )


def test_cli_doctor_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("clipapp_engine.cli.run_doctor", _fake_report)
    result = CliRunner().invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["encoders"][0] == {"name": "h264_amf", "usable": True, "error": None}
    assert data["gpus"] == ["AMD Radeon"]


def test_cli_doctor_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("clipapp_engine.cli.run_doctor", _fake_report)
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "h264_amf" in result.stdout
    assert "2.0 GiB" in result.stdout
