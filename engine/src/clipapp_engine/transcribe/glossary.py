"""Glossary: prompt terms that bias Whisper and replacements applied after transcription."""

import hashlib

from pydantic import BaseModel

from clipapp_engine.transcribe.transcript import Word

# Punctuation peeled off a word before comparing it with a replacement key.
_PUNCT = ".,;:!?¡¿\"'«»()[]…-"


class Glossary(BaseModel):
    """Domain vocabulary for a transcription.

    Attributes:
        terms: Names and jargon fed to Whisper's ``initial_prompt``.
        replacements: Misrecognition -> correct text. Keys match whole words (one or more,
            separated by spaces), case-insensitively; punctuation around them is kept.
    """

    terms: list[str] = []
    replacements: dict[str, str] = {}

    def prompt(self) -> str | None:
        """Return the ``initial_prompt`` built from the terms, or None when there are none."""
        return ", ".join(self.terms) + "." if self.terms else None

    def digest(self) -> str:
        """Return a short stable hash of the glossary, used in cache keys."""
        data = self.model_dump_json().encode()
        return hashlib.sha1(data, usedforsecurity=False).hexdigest()[:12]

    def apply(self, words: list[Word]) -> list[Word]:
        """Apply the replacements to a word list.

        A multi-word key (``"tre bor"``) merges the matched words into one word that spans
        from the first start to the last end, with the lowest probability.

        Args:
            words: Words in time order.

        Returns:
            A new list with the replacements applied (input is not modified).
        """
        keys = sorted(
            ((k.casefold().split(), v) for k, v in self.replacements.items() if k.split()),
            key=lambda kv: -len(kv[0]),  # longest key first so it wins over its sub-keys
        )
        bare = [w.text.strip(_PUNCT).casefold() for w in words]
        out: list[Word] = []
        i = 0
        while i < len(words):
            for tokens, value in keys:
                n = len(tokens)
                if bare[i : i + n] == tokens:
                    first, last = words[i], words[i + n - 1]
                    lead = first.text[: len(first.text) - len(first.text.lstrip(_PUNCT))]
                    trail = last.text[len(last.text.rstrip(_PUNCT)) :]
                    out.append(
                        Word(
                            text=f"{lead}{value}{trail}",
                            start_s=first.start_s,
                            end_s=last.end_s,
                            probability=min(w.probability for w in words[i : i + n]),
                        )
                    )
                    i += n
                    break
            else:
                out.append(words[i])
                i += 1
        return out
