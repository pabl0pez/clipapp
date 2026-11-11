"""Default parameters of silence removal."""

from pydantic import BaseModel, Field


class SilenceSettings(BaseModel):
    """User-tunable silence detection and cutting parameters.

    Attributes:
        threshold_db: Level (dBFS) below which audio counts as silence.
        min_silence_s: Shortest pause that is treated as silence.
        padding_s: Audio kept on each side of a silence so cuts do not clip words.
        min_keep_s: Kept segments shorter than this are dropped.
    """

    threshold_db: float = Field(default=-35.0, le=0)
    min_silence_s: float = Field(default=0.5, gt=0)
    padding_s: float = Field(default=0.15, ge=0)
    min_keep_s: float = Field(default=0.3, ge=0)
