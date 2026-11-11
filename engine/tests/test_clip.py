import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from clipapp_engine.cli import app
from clipapp_engine.config import ffmpeg_path, ffprobe_path
from clipapp_engine.export.clip import (
    ClipOptions,
    audio_filter,
    crop_window,
    output_size,
    render_clip,
    validate_range,
    video_filter,
)
from clipapp_engine.export.clips_file import ClipsFileError, parse_clips_file, parse_time
from clipapp_engine.export.pipeline import output_path, safe_name
from clipapp_engine.media.probe import probe
from clipapp_engine.subtitles.style import TEMPLATES
from clipapp_engine.transcribe.transcript import Transcript, Word

START, END = 2.0, 17.0  # 15 s inside the 20 s sample
BASE = ClipOptions(start_s=START, end_s=END, encoder="libx264", normalize=False)
WORDS = Transcript(
    model="test",
    language="es",
    words=[
        Word(text=t, start_s=START + i * 0.4, end_s=START + i * 0.4 + 0.35, probability=1)
        for i, t in enumerate(["¡Qué", "jugada", "increíble", "señores!"])
    ],
)


def _video(path: Path) -> dict[str, Any]:
    flags = ["-v", "error", "-show_streams", "-show_format", "-of", "json"]
    data = json.loads(
        subprocess.run(
            [str(ffprobe_path()), *flags, str(path)], check=True, capture_output=True
        ).stdout
    )
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    return {**video, "format_duration": float(data["format"]["duration"])}


def _frame_md5(path: Path, at_s: float) -> str:
    args = ["-v", "error", "-ss", str(at_s), "-i", str(path), "-frames:v", "1", "-f", "md5", "-"]
    return subprocess.run(
        [str(ffmpeg_path()), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture(scope="module")
def renders(av_sample: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("clips")
    accented = root / "Clips de ñandú ó"
    accented.mkdir()
    video = Path(shutil.copy(av_sample, accented / "partida épica 1.mkv"))
    cases = {
        "16:9": BASE.model_copy(update={"aspect": "16:9"}),
        "crop": BASE,
        "blur": BASE.model_copy(update={"crop_mode": "blur", "normalize": True}),
        "subs": BASE.model_copy(update={"style": TEMPLATES["shorts"]}),
    }
    outs = {}
    for key, options in cases.items():
        out = accented / f"salida {key.replace(':', 'x')} ñ.mp4"
        render_clip(video, options, out, WORDS if key == "subs" else None)
        outs[key] = out
    assert sorted(p.suffix for p in accented.iterdir()) == [".mkv"] + [".mp4"] * 4
    return outs


@pytest.mark.parametrize(
    ("key", "size"), [("16:9", (640, 360)), ("crop", (1080, 1920)), ("blur", (1080, 1920))]
)
def test_resolution_and_duration(renders: dict[str, Path], key: str, size: tuple[int, int]) -> None:
    info = _video(renders[key])
    assert (info["width"], info["height"]) == size
    assert info["avg_frame_rate"] == "30/1"
    assert abs(info["format_duration"] - (END - START)) < 0.1
    assert abs(float(info["duration"]) - (END - START)) < 0.1


def test_subtitles_burned_with_accented_paths(renders: dict[str, Path]) -> None:
    # Same framing with and without subtitles: a frame with a caption on screen differs.
    assert _frame_md5(renders["subs"], 0.5) != _frame_md5(renders["crop"], 0.5)


@pytest.mark.parametrize("size", [(640, 360), (1920, 1080), (2560, 1080), (300, 1000)])
def test_crop_window_positions_inside_frame(size: tuple[int, int]) -> None:
    width, height = size
    windows = [crop_window(width, height, x) for x in (0, 0.5, 1)]
    for w, h, x, y in windows:
        assert x >= 0 and x + w <= width
        assert y >= 0 and y + h <= height
        assert w % 2 == 0 and h % 2 == 0
        assert abs(w / h - 9 / 16) < 0.01
    xs = [x for _w, _h, x, _y in windows]
    assert xs[0] == 0 and xs[-1] == width - windows[0][0]
    if width * 16 > height * 9:  # wider than 9:16: the window can move
        assert len(set(xs)) == 3


def test_filters(av_sample: Path) -> None:
    info = probe(av_sample)
    assert "crop=202:360:438:0,scale=1080:1920" in video_filter(
        info, BASE.model_copy(update={"crop_x": 1})
    )
    assert video_filter(info, BASE, "clip.ass").endswith(
        ",subtitles=filename=clip.ass:fontsdir=fonts[v]"
    )
    fast = info.model_copy(update={"fps": 120})
    assert video_filter(fast, BASE).startswith("[0:v:0]fps=60,")
    assert "loudnorm=I=-14:TP=-1.5:LRA=11[a]" in audio_filter(
        info, BASE.model_copy(update={"normalize": True})
    )
    assert output_size(info.model_copy(update={"width": 3840, "height": 2160}), "16:9") == (
        1920,
        1080,
    )
    assert output_size(info, "16:9") == (640, 360)


def test_validate_range() -> None:
    validate_range(0, 15, 20)
    validate_range(100, 160)
    for start, end, duration in ((0, 14.9, None), (0, 60.5, None), (10, 5, None), (10, 30, 20)):
        with pytest.raises(ValueError):
            validate_range(start, end, duration)


def test_render_rejects_short_clip(av_sample: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="15-60"):
        render_clip(av_sample, BASE.model_copy(update={"end_s": 10}), tmp_path / "x.mp4")
    assert list(tmp_path.iterdir()) == []


def test_parse_time() -> None:
    assert parse_time("120") == 120
    assert parse_time("2:05") == 125
    assert parse_time("12:30.5") == 750.5
    assert parse_time("1:02:03") == 3723
    for bad in ("", "1:60", "abc", "-5", "1::2", "1:2:3:4", "2:5x"):
        with pytest.raises(ValueError):
            parse_time(bad)


def test_parse_clips_file() -> None:
    text = "# my clips\n\n12:30 13:05 persecucion-policia\n  100 130 jugada con espacios  \n"
    clips = parse_clips_file(text, 1000)
    assert [(c.line, c.start_s, c.end_s, c.name) for c in clips] == [
        (3, 750, 785, "persecucion-policia"),
        (4, 100, 130, "jugada con espacios"),
    ]


def test_parse_clips_file_reports_every_line() -> None:
    text = "0 20 ok\n0 10 corto\n1:00 x malo\n0 20\n990 1010 fuera\n# 0 1 comentado\n"
    with pytest.raises(ClipsFileError) as info:
        parse_clips_file(text, 1000)
    assert [e.split(":")[0] for e in info.value.errors] == ["line 2", "line 3", "line 4", "line 5"]
    with pytest.raises(ClipsFileError, match="no clips"):
        parse_clips_file("# nada\n")


def test_output_names(tmp_path: Path) -> None:
    assert safe_name('a:b/c?"d*') == "a_b_c__d_"
    assert safe_name("CON") == "_CON"
    assert safe_name("  .. ") == "clip"
    first = output_path(tmp_path, Path("Partida ñ.mkv"), "jefe: final", "9:16")
    assert first.name == "Partida ñ_jefe_ final_9x16.mp4"
    first.touch()
    second = output_path(tmp_path, Path("Partida ñ.mkv"), "jefe: final", "9:16")
    assert second.name == "Partida ñ_jefe_ final_9x16_2.mp4"
    second.touch()
    assert output_path(tmp_path, Path("Partida ñ.mkv"), "jefe: final", "9:16").stem.endswith("_3")


def test_cli_batch(av_sample: Path, tmp_path: Path) -> None:
    clips = tmp_path / "clips.txt"
    clips.write_text("# test\n0:00 0:15 uno\n3 18 dos\n", encoding="utf-8")
    out = tmp_path / "salida ñ"
    common = ["--style", "none", "--encoder", "libx264", "--aspect", "16:9", "-o", str(out)]
    result = CliRunner().invoke(app, ["batch", str(av_sample), "--clips", str(clips), *common])
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in out.iterdir()) == [
        "sample_dos_16x9.mp4",
        "sample_uno_16x9.mp4",
    ]

    clips.write_text("0 15 ok\n0 5 corto\n", encoding="utf-8")
    bad = CliRunner().invoke(app, ["batch", str(av_sample), "--clips", str(clips), *common])
    assert bad.exit_code == 2 and "line 2" in bad.output
    assert len(list(out.iterdir())) == 2  # nothing rendered when the file is invalid


def test_cli_clip_errors(av_sample: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    base = ["clip", str(av_sample), "-o", str(tmp_path), "--style", "none"]
    assert runner.invoke(app, [*base, "--start", "1:xx", "--end", "20"]).exit_code == 2
    assert (
        runner.invoke(app, [*base, "--start", "0", "--end", "15", "--aspect", "4:3"]).exit_code == 2
    )
    assert runner.invoke(app, [*base, "--start", "0", "--end", "15", "--style", "x"]).exit_code == 2
    # A range outside the video fails that clip; the summary reports it and exits 1.
    failed = runner.invoke(app, [*base, "--start", "10", "--end", "30"])
    assert failed.exit_code == 1 and "0 exported, 1 failed" in failed.output
