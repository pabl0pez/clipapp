"""Build styled .ass subtitles for a clip and the FFmpeg filter that burns them in."""

from pathlib import Path

import pysubs2

from clipapp_engine.subtitles.layout import Caption, layout
from clipapp_engine.subtitles.style import SubtitleStyle, hex_to_ass, to_color
from clipapp_engine.transcribe.transcript import Transcript

FONTS_DIR = Path(__file__).resolve().parents[3] / "assets" / "fonts"
REFERENCE_SHORT_SIDE = 1080
SIDE_MARGIN_PX = 60
DEFAULT_POSITION_Y = {"vertical": 0.70, "horizontal": 0.88}
SHADOW_ALPHA = 0x60
STYLE_NAME = "Clip"


def _ms(seconds: float, clip_start_s: float) -> int:
    return max(0, round((seconds - clip_start_s) * 1000))


def _text(caption: Caption, highlight: int | None, color: str) -> str:
    r"""Join rows with ``\\N``; the word at index ``highlight`` gets a color override."""
    index = 0
    lines = []
    for row in caption.rows:
        parts = []
        for word in row:
            parts.append(f"{{\\1c{color}}}{word.text}{{\\r}}" if index == highlight else word.text)
            index += 1
        lines.append(" ".join(parts))
    return "\\N".join(lines)


def _events(
    captions: list[Caption], style: SubtitleStyle, clip_start_s: float
) -> list[pysubs2.SSAEvent]:
    """Return one event per caption (``phrase``/``single_word``) or per word (``highlight``)."""
    events = []
    color = hex_to_ass(style.highlight_color)
    for cap in captions:
        if style.mode != "highlight":
            events.append(_event(cap.start_s, cap.end_s, _text(cap, None, color), clip_start_s))
            continue
        words = cap.words
        for i in range(len(words)):
            start = cap.start_s if i == 0 else words[i].start_s
            end = cap.end_s if i == len(words) - 1 else words[i + 1].start_s
            events.append(_event(start, end, _text(cap, i, color), clip_start_s))
    return events


def _event(start_s: float, end_s: float, text: str, clip_start_s: float) -> pysubs2.SSAEvent:
    return pysubs2.SSAEvent(
        start=_ms(start_s, clip_start_s), end=_ms(end_s, clip_start_s), text=text, style=STYLE_NAME
    )


def _ass_style(style: SubtitleStyle, width: int, height: int) -> pysubs2.SSAStyle:
    """Translate a ``SubtitleStyle`` into an ASS style scaled to the output resolution."""
    scale = min(width, height) / REFERENCE_SHORT_SIDE
    aspect = "vertical" if height > width else "horizontal"
    position_y = style.position_y if style.position_y is not None else DEFAULT_POSITION_Y[aspect]
    back = style.highlight_color if style.mode == "single_word" else style.outline_color
    return pysubs2.SSAStyle(
        fontname=style.font,
        fontsize=round(style.size * scale),
        primarycolor=to_color(style.primary_color),
        secondarycolor=to_color(style.highlight_color),
        outlinecolor=to_color(style.outline_color),
        backcolor=to_color(back, 0 if style.mode == "single_word" else SHADOW_ALPHA),
        bold=False,
        outline=round(style.outline * scale, 1),
        shadow=round(style.shadow * scale, 1),
        alignment=pysubs2.Alignment.BOTTOM_CENTER,
        marginl=round(SIDE_MARGIN_PX * scale),
        marginr=round(SIDE_MARGIN_PX * scale),
        marginv=round((1 - position_y) * height),
    )


def build_ass(
    transcript: Transcript, style: SubtitleStyle, width: int, height: int, clip_start_s: float
) -> pysubs2.SSAFile:
    """Lay out a clip's words and return them as a styled ASS document.

    Args:
        transcript: Words on the original video's clock.
        style: Look and splitting rules.
        width: Output width in pixels (``PlayResX``), e.g. 1080 for 9:16.
        height: Output height in pixels (``PlayResY``), e.g. 1920 for 9:16.
        clip_start_s: Clip start on the original clock; event times are relative to it.

    Returns:
        The subtitle file; save it with ``.save(path)`` or ``.to_string("ass")``.
    """
    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = str(width)
    subs.info["PlayResY"] = str(height)
    subs.info["ScaledBorderAndShadow"] = "yes"
    # Smart wrapping only kicks in if a line is still too wide; it keeps text inside the frame.
    subs.info["WrapStyle"] = "0"
    subs.styles = {STYLE_NAME: _ass_style(style, width, height)}
    subs.events = _events(layout(transcript.words, style), style, clip_start_s)
    return subs


def _filter_value(path: Path) -> str:
    """Escape a path for a filter option value, then quote it for the filtergraph parser."""
    value = path.resolve().as_posix().replace("\\", "\\\\").replace(":", "\\:")
    value = value.replace("'", "\\'")
    return "'" + value.replace("'", "'\\''") + "'"


def burn_filter(ass_path: Path, fonts_dir: Path = FONTS_DIR) -> str:
    """Return the ``subtitles`` filter that draws ``ass_path`` with the bundled fonts."""
    return f"subtitles=filename={_filter_value(ass_path)}:fontsdir={_filter_value(fonts_dir)}"
