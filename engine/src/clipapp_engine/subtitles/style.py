"""Subtitle style model, color conversion and the factory templates."""

from typing import Literal

import pysubs2
from pydantic import BaseModel, Field

HexColor = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
Mode = Literal["phrase", "highlight", "single_word"]


class SubtitleStyle(BaseModel):
    """How a clip's subtitles look and are split.

    Sizes (``size``, ``outline``, ``shadow``) are pixels for a 1080 px short side; they scale
    with the output resolution.

    Attributes:
        name: Display name.
        font: Font family name as libass sees it (a file in ``engine/assets/fonts``).
        size: Font size.
        primary_color: Text color, ``#RRGGBB``.
        highlight_color: Color of the word being spoken in ``highlight`` mode; in
            ``single_word`` mode it colors the shadow instead. ``#RRGGBB``.
        outline_color: Border and shadow color, ``#RRGGBB``.
        outline: Border thickness.
        shadow: Shadow offset.
        position_y: Bottom of the text as a fraction of the height from the top; ``None`` uses the
            aspect default (0.70 in 9:16, 0.88 in 16:9).
        mode: ``phrase`` (whole line), ``highlight`` (whole line, current word colored) or
            ``single_word`` (one big word at a time).
        max_chars: Maximum characters per line.
        max_lines: Maximum lines on screen at once.
        uppercase: Show text in upper case without final punctuation.
    """

    name: str
    font: str
    size: float = Field(gt=0)
    primary_color: str = HexColor
    highlight_color: str = HexColor
    outline_color: str = HexColor
    outline: float = Field(ge=0)
    shadow: float = Field(ge=0)
    position_y: float | None = Field(default=None, ge=0, le=1)
    mode: Mode
    max_chars: int = Field(gt=0)
    max_lines: int = Field(gt=0)
    uppercase: bool


def to_color(hex_color: str, alpha: int = 0) -> pysubs2.Color:
    """Convert ``#RRGGBB`` to a pysubs2 color (alpha 0 = opaque, 255 = transparent)."""
    return pysubs2.Color(
        int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16), alpha
    )


def hex_to_ass(hex_color: str) -> str:
    """Convert ``#RRGGBB`` to the ASS override-tag form ``&HBBGGRR&``."""
    return f"&H{hex_color[5:7]}{hex_color[3:5]}{hex_color[1:3]}&".upper()


TEMPLATES: dict[str, SubtitleStyle] = {
    "shorts": SubtitleStyle(
        name="Shorts",
        font="Montserrat ExtraBold",
        size=74,
        primary_color="#FFFFFF",
        highlight_color="#FFE600",
        outline_color="#000000",
        outline=7,
        shadow=0,
        mode="highlight",
        max_chars=18,
        max_lines=2,
        uppercase=True,
    ),
    "limpio": SubtitleStyle(
        name="Limpio",
        font="Inter SemiBold",
        size=56,
        primary_color="#FFFFFF",
        highlight_color="#FFFFFF",
        outline_color="#000000",
        outline=1,
        shadow=3,
        mode="phrase",
        max_chars=32,
        max_lines=2,
        uppercase=False,
    ),
    "gamer": SubtitleStyle(
        name="Gamer",
        font="Anton",
        size=140,
        primary_color="#FFFFFF",
        highlight_color="#39FF14",
        outline_color="#000000",
        outline=8,
        shadow=6,
        mode="single_word",
        max_chars=18,
        max_lines=1,
        uppercase=True,
    ),
}
