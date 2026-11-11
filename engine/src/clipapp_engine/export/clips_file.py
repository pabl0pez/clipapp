"""Parse clip times (``120``, ``2:00``, ``1:02:00.5``) and ``clips.txt`` batch files."""

import re
from dataclasses import dataclass

from clipapp_engine.export.clip import validate_range

_INT = re.compile(r"\d+")
_NUM = re.compile(r"\d+(\.\d+)?")


def parse_time(text: str) -> float:
    """Return seconds from ``ss``, ``mm:ss`` or ``hh:mm:ss`` (seconds may have decimals).

    Raises:
        ValueError: malformed time, or minutes/seconds of 60 or more after a colon.
    """
    parts = text.strip().split(":")
    valid = (
        len(parts) <= 3
        and all(_INT.fullmatch(p) for p in parts[:-1])
        and _NUM.fullmatch(parts[-1]) is not None
        and all(float(p) < 60 for p in parts[1:])
    )
    if not valid:
        raise ValueError(f"invalid time {text!r} (use seconds or mm:ss)")
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


@dataclass(frozen=True)
class ClipSpec:
    """One line of a clips file.

    Attributes:
        line: 1-based line number.
        start_s: Clip start in seconds.
        end_s: Clip end in seconds.
        name: Clip name (the rest of the line, may contain spaces).
    """

    line: int
    start_s: float
    end_s: float
    name: str


class ClipsFileError(ValueError):
    """A clips file has invalid lines.

    Args:
        errors: One ``line N: message`` entry per invalid line.
    """

    def __init__(self, errors: list[str]) -> None:
        """Store the per-line errors."""
        self.errors = errors
        super().__init__("\n".join(errors))


def parse_clips_file(text: str, duration_s: float | None = None) -> list[ClipSpec]:
    """Parse a clips file: ``start end name`` per line; blank and ``#`` lines are ignored.

    Every line is checked before returning, so all errors are reported at once.

    Args:
        text: File contents.
        duration_s: Video duration; clips ending after it are errors.

    Returns:
        The clips in file order.

    Raises:
        ClipsFileError: some line is malformed or its range is invalid, or there is no clip.
    """
    clips: list[ClipSpec] = []
    errors: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(maxsplit=2)
        try:
            if len(fields) < 3:
                raise ValueError("expected: start end name (e.g. 12:30 13:05 chase)")
            start, end = parse_time(fields[0]), parse_time(fields[1])
            validate_range(start, end, duration_s)
        except ValueError as exc:
            errors.append(f"line {number}: {exc}")
            continue
        clips.append(ClipSpec(number, start, end, fields[2]))
    if not clips and not errors:
        errors.append("no clips in file")
    if errors:
        raise ClipsFileError(errors)
    return clips
