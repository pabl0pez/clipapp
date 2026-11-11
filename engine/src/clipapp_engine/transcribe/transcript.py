"""Transcript data models shared by transcription, caching and the glossary."""

from pydantic import BaseModel


class Word(BaseModel):
    """One recognized word, timed on the original video's clock.

    Attributes:
        text: Word text without surrounding spaces (punctuation kept).
        start_s: Start time in seconds.
        end_s: End time in seconds.
        probability: Model confidence in [0, 1].
    """

    text: str
    start_s: float
    end_s: float
    probability: float


class Transcript(BaseModel):
    """Words recognized inside a clip range.

    Attributes:
        model: Whisper model that produced it (after any fallback).
        language: Language code, e.g. ``es``.
        words: Words sorted by time.
    """

    model: str
    language: str
    words: list[Word]

    @property
    def text(self) -> str:
        """Return the words joined by spaces."""
        return " ".join(w.text for w in self.words)
