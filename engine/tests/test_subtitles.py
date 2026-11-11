import itertools
from pathlib import Path

import pysubs2
import pytest
from typer.testing import CliRunner

from clipapp_engine.cli import app
from clipapp_engine.media.runner import run_ffmpeg
from clipapp_engine.subtitles.ass import FONTS_DIR, build_ass, burn_filter
from clipapp_engine.subtitles.layout import layout
from clipapp_engine.subtitles.srt import build_srt
from clipapp_engine.subtitles.style import TEMPLATES, hex_to_ass, to_color
from clipapp_engine.transcribe.transcript import Transcript, Word

CLIP_START = 120.0


def _words(spec: list[tuple[str, float]]) -> list[Word]:
    """Words of 0.3 s each starting at the given offsets after CLIP_START."""
    return [
        Word(text=t, start_s=CLIP_START + s, end_s=CLIP_START + s + 0.3, probability=1)
        for t, s in spec
    ]


# Pause of 1 s after "ronda." and a long run that must be split by size and duration.
SAMPLE = Transcript(
    model="test",
    language="es",
    words=_words(
        [
            ("¿Viste", 0.0),
            ("eso?", 0.35),
            ("Increíblemente", 0.7),
            ("conseguimos", 1.05),
            ("ganar", 1.4),
            ("la", 1.75),
            ("última", 2.1),
            ("ronda.", 2.45),
            ("Vamos", 3.75),
            ("a", 4.1),
            ("por", 4.45),
            ("la", 4.8),
            ("siguiente", 5.15),
            ("partida", 5.5),
            ("ahora", 5.85),
            ("mismo", 6.2),
            ("con", 6.55),
            ("todo", 6.9),
        ]
    ),
)


@pytest.mark.parametrize("key", list(TEMPLATES))
def test_layout_respects_limits_and_time(key: str) -> None:
    style = TEMPLATES[key]
    captions = layout(SAMPLE.words, style)
    assert sum(len(c.words) for c in captions) == len(SAMPLE.words)
    for cap in captions:
        assert len(cap.rows) <= style.max_lines
        assert all(len(" ".join(w.text for w in row)) <= style.max_chars for row in cap.rows)
        assert cap.start_s < cap.end_s <= cap.start_s + 3.0
    for prev, nxt in itertools.pairwise(captions):
        assert prev.end_s <= nxt.start_s


def test_layout_breaks_on_pause_punctuation_and_min_duration() -> None:
    style = TEMPLATES["limpio"].model_copy(update={"max_chars": 100})
    captions = layout(SAMPLE.words, style)
    texts = [" ".join(w.text for w in c.words) for c in captions]
    assert texts[0] == "¿Viste eso?"
    assert texts[1] == "Increíblemente conseguimos ganar la última ronda."
    # Last word alone would last 0.3 s; it is stretched to 0.6 s.
    lone = layout(SAMPLE.words[-1:], style)[0]
    assert lone.end_s - lone.start_s == pytest.approx(0.6)


def test_uppercase_strips_edge_punctuation() -> None:
    captions = layout(SAMPLE.words[:2], TEMPLATES["shorts"])
    assert [w.text for w in captions[0].words] == ["VISTE", "ESO"]


def test_event_counts_per_mode() -> None:
    base = TEMPLATES["limpio"]
    n_captions = len(layout(SAMPLE.words, base))
    expected = {
        "phrase": n_captions,
        "highlight": len(SAMPLE.words),
        "single_word": len(SAMPLE.words),
    }
    for mode, count in expected.items():
        style = base.model_copy(update={"mode": mode})
        subs = build_ass(SAMPLE, style, 1080, 1920, CLIP_START)
        assert len(subs.events) == count
        starts = [e.start for e in subs.events]
        assert starts == sorted(starts) and starts[0] == 0
        assert all(a.end <= b.start for a, b in zip(subs.events, subs.events[1:], strict=False))


def test_highlight_marks_one_word_per_event() -> None:
    subs = build_ass(SAMPLE, TEMPLATES["shorts"], 1080, 1920, CLIP_START)
    color = hex_to_ass(TEMPLATES["shorts"].highlight_color)
    assert all(e.text.count(color) == 1 for e in subs.events)
    assert subs.events[0].text.startswith(f"{{\\1c{color}}}VISTE{{\\r}}")


def test_color_conversion() -> None:
    assert hex_to_ass("#FFE600") == "&H00E6FF&"
    assert hex_to_ass("#12ab9C") == "&H9CAB12&"
    assert to_color("#FFE600") == pysubs2.Color(255, 230, 0, 0)
    with pytest.raises(ValueError, match="pattern"):
        TEMPLATES["shorts"].model_validate(
            {**TEMPLATES["shorts"].model_dump(), "primary_color": "red"}
        )


def test_ass_roundtrip_and_layout_values() -> None:
    text = build_ass(SAMPLE, TEMPLATES["shorts"], 1080, 1920, CLIP_START).to_string("ass")
    assert "&H0000E6FF" in text  # highlight as BGR secondary color in the style line
    subs = pysubs2.SSAFile.from_string(text)
    assert subs.info["PlayResX"] == "1080" and subs.info["PlayResY"] == "1920"
    style = subs.styles["Clip"]
    assert (style.marginl, style.marginr, style.marginv) == (60, 60, 576)
    assert style.alignment == pysubs2.Alignment.BOTTOM_CENTER
    wide = build_ass(SAMPLE, TEMPLATES["shorts"], 1280, 720, CLIP_START).styles["Clip"]
    assert wide.fontsize == round(74 * 720 / 1080)


def test_srt_uses_phrase_mode() -> None:
    srt = build_srt(SAMPLE, TEMPLATES["shorts"], CLIP_START)
    subs = pysubs2.SSAFile.from_string(srt)
    assert len(subs) == len(layout(SAMPLE.words, TEMPLATES["shorts"]))
    assert "\\1c" not in srt and "VISTE ESO" in srt


def test_fonts_bundled() -> None:
    for name in ("Montserrat-ExtraBold.ttf", "Inter-SemiBold.ttf", "Anton-Regular.ttf"):
        assert (FONTS_DIR / name).is_file()


def test_burn_two_seconds(accented_sample: Path, tmp_path: Path) -> None:
    ass = accented_sample.parent / "subs 'ñ'.ass"
    short = SAMPLE.model_copy(update={"words": SAMPLE.words[:6]})
    build_ass(short, TEMPLATES["shorts"], 640, 360, CLIP_START).save(str(ass))
    out = tmp_path / "burned.mp4"
    run_ffmpeg(
        [
            "-i",
            str(accented_sample),
            "-t",
            "2",
            "-vf",
            burn_filter(ass),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(out),
        ],
        2.0,
    )
    assert out.stat().st_size > 0


def test_cli_subs(tmp_path: Path) -> None:
    t = tmp_path / "t.json"
    t.write_text(SAMPLE.model_dump_json(), encoding="utf-8")
    runner = CliRunner()
    listed = runner.invoke(app, ["subs", "--list-styles"])
    assert listed.exit_code == 0 and "shorts" in listed.output and "gamer" in listed.output
    for name in ("subs.ass", "subs.srt"):
        out = tmp_path / name
        args = [
            "subs",
            "--transcript",
            str(t),
            "--style",
            "gamer",
            "--aspect",
            "16:9",
            "--clip-start",
            str(CLIP_START),
            "-o",
            str(out),
        ]
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        assert len(pysubs2.load(str(out))) > 0
    bad = runner.invoke(app, ["subs", "--transcript", str(t), "--style", "x", "-o", str(out)])
    assert bad.exit_code == 2
