"""Pure functions turning silences into the ranges of video to keep."""

from pydantic import BaseModel

Range = tuple[float, float]


class SilenceStats(BaseModel):
    """Summary of a cut: durations in seconds, ``removed_pct`` in 0..100."""

    original_s: float
    final_s: float
    removed_pct: float
    cuts: int


def _merge(ranges: list[Range]) -> list[Range]:
    """Sort and merge overlapping or touching ranges."""
    merged: list[Range] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def compute_keep_ranges(
    duration_s: float, silences: list[Range], padding_s: float, min_keep_s: float
) -> list[Range]:
    """Compute the ranges to keep from detected silences.

    Each silence shrinks by ``padding_s`` per side (dropped if nothing is left), except at the
    file edges where there is no speech to protect; the result is
    inverted within ``[0, duration_s]``, kept ranges shorter than ``min_keep_s`` are removed and
    touching ranges are merged.

    Args:
        duration_s: Media duration.
        silences: ``(start_s, end_s)`` silences, in any order, possibly overlapping.
        padding_s: Audio kept around each silence.
        min_keep_s: Shortest kept range.

    Returns:
        Sorted, disjoint keep ranges inside ``[0, duration_s]``.
    """
    shrunk = [
        (s + padding_s if s > 0 else 0.0, e - padding_s if e < duration_s else duration_s)
        for s, e in silences
    ]
    cuts = _merge([(s, e) for s, e in shrunk if e > s])
    keep: list[Range] = []
    cursor = 0.0
    for s, e in [*cuts, (duration_s, duration_s)]:
        if s > cursor and s - cursor >= min_keep_s:
            keep.append((cursor, s))
        cursor = max(cursor, e)
    return _merge(keep)


def stats(duration_s: float, keep_ranges: list[Range]) -> SilenceStats:
    """Summarize a cut.

    Args:
        duration_s: Original duration.
        keep_ranges: Output of ``compute_keep_ranges``.

    Returns:
        Original and final duration, percent removed and the number of removed stretches.
    """
    final = sum(e - s for s, e in keep_ranges)
    bounds = [0.0, *(t for r in keep_ranges for t in r), duration_s]
    cuts = sum(1 for a, b in zip(bounds[::2], bounds[1::2], strict=True) if b - a > 1e-9)
    removed = 100 * (1 - final / duration_s) if duration_s > 0 else 0.0
    return SilenceStats(original_s=duration_s, final_s=final, removed_pct=removed, cuts=cuts)
