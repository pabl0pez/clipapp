"""Pure functions that split timed words into on-screen captions."""

from dataclasses import dataclass

from clipapp_engine.subtitles.style import SubtitleStyle
from clipapp_engine.transcribe.transcript import Word

MAX_PAUSE_S = 0.4
MIN_DURATION_S = 0.6
MAX_DURATION_S = 3.0
STRONG_PUNCTUATION = ".?!…;"
EDGE_PUNCTUATION = ".,;:!?…¡¿\"'"


@dataclass(frozen=True)
class Caption:
    """One subtitle on screen.

    Attributes:
        start_s: Appearance time (same clock as the words).
        end_s: Disappearance time; never after the next caption's start.
        rows: Lines of words; each word keeps its own timing and its display text.
    """

    start_s: float
    end_s: float
    rows: list[list[Word]]

    @property
    def words(self) -> list[Word]:
        """Return all words in reading order."""
        return [w for row in self.rows for w in row]


def _row_len(row: list[Word]) -> int:
    return sum(len(w.text) for w in row) + len(row) - 1


def _place(rows: list[list[Word]], word: Word, style: SubtitleStyle) -> bool:
    """Add ``word`` to the caption being built; return False when it must start a new one."""
    prev = rows[-1][-1]
    if (
        style.mode == "single_word"
        or word.start_s - prev.end_s > MAX_PAUSE_S
        or prev.text.endswith(tuple(STRONG_PUNCTUATION))
        or word.end_s - rows[0][0].start_s > MAX_DURATION_S
    ):
        return False
    if _row_len([*rows[-1], word]) <= style.max_chars:
        rows[-1].append(word)
    elif len(rows) < style.max_lines:
        rows.append([word])
    else:
        return False
    return True


def _group(words: list[Word], style: SubtitleStyle) -> list[list[list[Word]]]:
    """Greedily pack words into captions of at most ``max_lines`` rows of ``max_chars``."""
    groups: list[list[list[Word]]] = []
    for word in words:
        if not groups or not _place(groups[-1], word, style):
            # shortcut: a single word longer than max_chars still gets its own row, unsplit.
            groups.append([[word]])
    return groups


def _display(rows: list[list[Word]], uppercase: bool) -> list[list[Word]]:
    """Apply upper case and drop the caption's edge punctuation (``¿``/``¡`` and the final one)."""
    if not uppercase:
        return rows
    out = [[w.model_copy(update={"text": w.text.upper()}) for w in row] for row in rows]
    first = out[0][0]
    out[0][0] = first.model_copy(update={"text": first.text.lstrip("¡¿") or first.text})
    last = out[-1][-1]
    out[-1][-1] = last.model_copy(update={"text": last.text.rstrip(EDGE_PUNCTUATION) or last.text})
    return out


def layout(words: list[Word], style: SubtitleStyle) -> list[Caption]:
    """Split words into captions that are readable and never overlap.

    A new caption starts after a pause longer than 0.4 s, after strong punctuation, when the
    caption would last more than 3 s, or when the words no longer fit in ``max_lines`` rows of
    ``max_chars``. In ``single_word`` mode every word is its own caption. Each caption lasts at
    least 0.6 s and at most 3 s, but is cut at the next caption's start.

    Args:
        words: Words sorted by start time (empty texts are ignored).
        style: Provides ``mode``, ``max_chars``, ``max_lines`` and ``uppercase``.

    Returns:
        Captions in time order, with display text already applied.
    """
    clean = [w.model_copy(update={"text": w.text.strip()}) for w in words if w.text.strip()]
    groups = [_display(rows, style.uppercase) for rows in _group(clean, style)]
    captions: list[Caption] = []
    for i, rows in enumerate(groups):
        start = rows[0][0].start_s
        end = min(max(rows[-1][-1].end_s, start + MIN_DURATION_S), start + MAX_DURATION_S)
        if i + 1 < len(groups):
            end = min(end, groups[i + 1][0][0].start_s)
        captions.append(Caption(start, end, rows))
    return captions
