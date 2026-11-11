import pytest

from clipapp_engine.silence.ranges import Range, compute_keep_ranges, stats
from clipapp_engine.silence.settings import SilenceSettings


def keep(silences: list[Range], padding: float = 0.0, min_keep: float = 0.0) -> list[Range]:
    return compute_keep_ranges(10.0, silences, padding, min_keep)


def test_middle_silence_with_padding() -> None:
    assert keep([(3, 5)], padding=0.25) == [(0, 3.25), (4.75, 10)]


def test_silence_at_start_and_end() -> None:
    assert keep([(0, 2), (8, 10)], padding=0.5) == [(1.5, 8.5)]


def test_no_silences() -> None:
    assert keep([]) == [(0, 10)]


def test_all_silence() -> None:
    assert keep([(0, 10)]) == []


def test_overlapping_silences() -> None:
    assert keep([(4, 6), (2, 5), (5.5, 7)]) == [(0, 2), (7, 10)]


def test_padding_larger_than_silence_drops_it() -> None:
    assert keep([(3, 3.4), (6, 8)], padding=0.3) == [(0, 6.3), (7.7, 10)]


def test_min_keep_drops_short_ranges() -> None:
    assert keep([(1, 3), (3.2, 6)], min_keep=0.3) == [(0, 1), (6, 10)]
    assert keep([(1, 3), (3.2, 6)], min_keep=0.2) == [(0, 1), (3, 3.2), (6, 10)]


def test_clamps_to_duration() -> None:
    assert keep([(-1, 1), (9, 12)]) == [(1, 9)]


def test_stats() -> None:
    s = stats(10.0, [(1, 4), (6, 9)])
    assert (s.original_s, s.final_s, s.cuts) == (10, 6, 3)
    assert s.removed_pct == pytest.approx(40)
    assert stats(10.0, [(0, 10)]).cuts == 0
    assert stats(10.0, []).removed_pct == 100
    assert stats(0.0, []).removed_pct == 0


def test_settings_defaults() -> None:
    s = SilenceSettings()
    assert (s.threshold_db, s.min_silence_s, s.padding_s, s.min_keep_s) == (-35, 0.5, 0.15, 0.3)
