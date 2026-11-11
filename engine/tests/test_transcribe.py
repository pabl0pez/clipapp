import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import ctranslate2
import pytest
from typer.testing import CliRunner

from clipapp_engine.cli import app
from clipapp_engine.config import data_dir, ffmpeg_path
from clipapp_engine.transcribe import device, download, whisper
from clipapp_engine.transcribe.device import DeviceChoice, pick_device
from clipapp_engine.transcribe.glossary import Glossary
from clipapp_engine.transcribe.transcript import Word

CPU = DeviceChoice("small", "cpu", "int8")
GPU = DeviceChoice("large-v3-turbo", "cuda", "int8_float16")


def _w(word: str, start: float, end: float, p: float = 0.9) -> SimpleNamespace:
    return SimpleNamespace(word=word, start=start, end=end, probability=p)


class FakeModel:
    """Stands in for WhisperModel; words are in seconds relative to the given audio."""

    instances: ClassVar[list["FakeModel"]] = []
    oom_models: ClassVar[set[str]] = set()

    def __init__(self, path: str, device: str, compute_type: str) -> None:
        self.name = Path(path).name
        self.calls: list[dict[str, Any]] = []
        FakeModel.instances.append(self)

    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[Any, None]:
        if self.name in FakeModel.oom_models:
            raise RuntimeError("CUDA failed with error out of memory")
        self.calls.append({"samples": len(audio), **kwargs})
        words = [
            _w(" Hola", 0.5, 0.9),  # abs 4.5-4.9: before the range
            _w(" tre", 1.2, 1.4),
            _w(" bor,", 1.4, 1.6, 0.5),
            _w(" qué", 2.0, 2.3),
            _w(" tal", 5.5, 6.2),  # abs 9.5-10.2: midpoint inside, clamped to 10
            _w(" adiós", 5.8, 6.4),  # midpoint 10.1: outside
        ]
        return iter([SimpleNamespace(words=words)]), None


@pytest.fixture(autouse=True)
def fake_whisper(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    FakeModel.instances, FakeModel.oom_models = [], set()
    monkeypatch.setattr(whisper, "WhisperModel", FakeModel)
    monkeypatch.setattr(whisper, "ensure_model", lambda name: Path("models") / name)
    whisper._MODELS.clear()
    shutil.rmtree(data_dir() / "cache", ignore_errors=True)
    yield
    whisper._MODELS.clear()


def test_times_on_original_clock_and_clipped(av_sample: Path) -> None:
    t = whisper.transcribe_range(av_sample, 1, 5.0, 10.0, choice=CPU)
    assert [w.text for w in t.words] == ["tre", "bor,", "qué", "tal"]
    assert t.words[0].start_s == pytest.approx(5.2)
    assert t.words[-1].start_s == pytest.approx(9.5)
    assert t.words[-1].end_s == 10.0
    assert (t.model, t.language) == ("small", "es")
    call = FakeModel.instances[0].calls[0]
    assert call["samples"] == 7 * 16_000  # 4..11 s: 1 s margin on each side
    assert call["language"] == "es"
    assert call["word_timestamps"] and call["vad_filter"]
    assert call["condition_on_previous_text"] is False


def test_margin_clamped_at_zero(av_sample: Path) -> None:
    whisper.transcribe_range(av_sample, 1, 0.5, 3.0, choice=CPU)
    assert FakeModel.instances[0].calls[0]["samples"] == 4 * 16_000  # 0..4 s


def test_glossary_prompt_and_replacement(av_sample: Path) -> None:
    g = Glossary(terms=["Trevor", "Lamar"], replacements={"Tre Bor": "Trevor"})
    t = whisper.transcribe_range(av_sample, 1, 5.0, 10.0, g, CPU)
    assert t.text == "Trevor, qué tal"
    assert t.words[0].start_s == pytest.approx(5.2)
    assert t.words[0].end_s == pytest.approx(5.6)
    assert t.words[0].probability == 0.5
    assert FakeModel.instances[0].calls[0]["initial_prompt"] == "Trevor, Lamar."


def test_cache_skips_model_and_model_stays_loaded(av_sample: Path) -> None:
    first = whisper.transcribe_range(av_sample, 1, 5.0, 10.0, choice=CPU)
    assert whisper.transcribe_range(av_sample, 1, 5.0, 10.0, choice=CPU) == first
    assert len(FakeModel.instances[0].calls) == 1
    whisper.transcribe_range(av_sample, 1, 6.0, 10.0, choice=CPU)
    assert len(FakeModel.instances) == 1  # same loaded model reused
    assert len(FakeModel.instances[0].calls) == 2
    other = whisper.transcribe_range(av_sample, 1, 5.0, 10.0, Glossary(terms=["x"]), CPU)
    assert other.text == first.text and len(FakeModel.instances[0].calls) == 3


def test_gpu_out_of_memory_falls_back_to_small(av_sample: Path) -> None:
    FakeModel.oom_models = {"large-v3-turbo"}
    warnings: list[str] = []
    t = whisper.transcribe_range(av_sample, 1, 5.0, 10.0, choice=GPU, on_warning=warnings.append)
    assert t.model == "small"
    assert FakeModel.instances[-1].name == "small"
    assert "out of memory" in warnings[0]


def test_other_runtime_errors_propagate(av_sample: Path) -> None:
    FakeModel.oom_models = {"small"}
    with pytest.raises(RuntimeError):
        whisper.transcribe_range(av_sample, 1, 5.0, 10.0, choice=replace(GPU, model="small"))


def test_invalid_range(av_sample: Path) -> None:
    with pytest.raises(ValueError, match="Invalid range"):
        whisper.transcribe_range(av_sample, 1, 10.0, 5.0, choice=CPU)


def test_glossary_word_boundaries_and_case() -> None:
    words = [
        Word(text=t, start_s=i, end_s=i + 1, probability=1)
        for i, t in enumerate(["¿Lamar?", "lamarcito", "LAMAR"])
    ]
    out = Glossary(replacements={"lamar": "Lamar Davis"}).apply(words)
    assert [w.text for w in out] == ["¿Lamar Davis?", "lamarcito", "Lamar Davis"]
    assert Glossary().prompt() is None


# --- pick_device -------------------------------------------------------------------------


@pytest.fixture
def no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in (device.MODEL_ENV, device.DEVICE_ENV, device.COMPUTE_ENV):
        monkeypatch.delenv(env, raising=False)


@pytest.mark.usefixtures("no_env")
def test_pick_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device, "cuda_problem", lambda: None)
    assert pick_device() == GPU


@pytest.mark.usefixtures("no_env")
def test_pick_cpu_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device, "cuda_problem", lambda: "cudnn_ops64_9.dll not found")
    choice = pick_device()
    assert (choice.model, choice.device, choice.compute_type) == ("small", "cpu", "int8")
    assert "cudnn_ops64_9.dll" in choice.warnings[0]


@pytest.mark.usefixtures("no_env")
def test_cuda_problem_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device, "register_cuda_dlls", list)
    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 0)
    assert "no NVIDIA GPU" in (device.cuda_problem() or "")

    def boom() -> int:
        raise RuntimeError("driver too old")

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", boom)
    assert "driver too old" in (device.cuda_problem() or "")


@pytest.mark.usefixtures("no_env")
def test_pick_forced_by_args_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> str:
        raise AssertionError("detection must be skipped when the device is forced")

    monkeypatch.setattr(device, "cuda_problem", fail)
    assert pick_device("medium", "cpu") == DeviceChoice("medium", "cpu", "int8")
    monkeypatch.setenv(device.DEVICE_ENV, "cpu")
    monkeypatch.setenv(device.COMPUTE_ENV, "float32")
    assert pick_device() == DeviceChoice("small", "cpu", "float32")
    assert pick_device(compute_type="int8").compute_type == "int8"


def test_ensure_model_downloads_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_download(name: str, output_dir: str) -> str:
        calls.append(name)
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        (Path(output_dir) / "model.bin").write_bytes(b"x")
        return output_dir

    monkeypatch.setattr(download, "download_model", fake_download)
    folder = download.ensure_model("tiny")
    assert download.ensure_model("tiny") == folder == data_dir() / "models" / "tiny"
    assert calls == ["tiny"]


# --- CLI ---------------------------------------------------------------------------------


def test_cli_transcribe(av_sample: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device, "cuda_problem", lambda: "no NVIDIA GPU with CUDA was found")
    gfile = tmp_path / "glosario.json"
    gfile.write_text(json.dumps({"replacements": {"tre bor": "Trevor"}}), encoding="utf-8")
    args = ["transcribe", str(av_sample), "--start", "5", "--end", "10", "--glossary", str(gfile)]
    result = CliRunner().invoke(app, [*args, "--device", "cpu", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["words"][0]["text"] == "Trevor,"
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "Trevor, qué tal" in result.stdout
    assert "CPU" in result.stderr


def test_cli_transcribe_bad_range(av_sample: Path) -> None:
    result = CliRunner().invoke(
        app, ["transcribe", str(av_sample), "--start", "9", "--end", "3", "--device", "cpu"]
    )
    assert result.exit_code == 1
    assert "Invalid range" in result.output


# --- real model --------------------------------------------------------------------------

VOICE = Path(__file__).parents[2] / "samples" / "voz.wav"
# Read before the session fixture isolates APPDATA, so the real model folder is reused.
REAL_MODELS = (
    Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "clipapp" / "models"
)


@pytest.mark.slow
@pytest.mark.skipif(not VOICE.is_file(), reason="samples/voz.wav not present")
def test_real_whisper_on_voice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.undo()  # real WhisperModel
    monkeypatch.setattr(download, "model_dir", lambda name: REAL_MODELS / name)
    media = tmp_path / "voz.mkv"
    subprocess.run(
        [str(ffmpeg_path()), "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=320x180",
         "-i", str(VOICE), "-map", "0:v", "-map", "1:a", "-map", "1:a", "-shortest",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(media)],
        check=True,
    )  # fmt: skip
    t = whisper.transcribe_range(media, 1, 0.0, 10.0)
    assert t.words, "no words recognized"
    assert all(0.0 <= w.start_s <= w.end_s <= 10.0 for w in t.words)
