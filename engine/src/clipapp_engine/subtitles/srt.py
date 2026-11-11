"""Export a clip's subtitles as plain .srt (phrase mode, no styling)."""

from clipapp_engine.subtitles.ass import build_ass
from clipapp_engine.subtitles.style import SubtitleStyle
from clipapp_engine.transcribe.transcript import Transcript


def build_srt(transcript: Transcript, style: SubtitleStyle, clip_start_s: float) -> str:
    """Return SRT text with one cue per caption, using the style's line-splitting rules.

    Args:
        transcript: Words on the original video's clock.
        style: Only ``max_chars``, ``max_lines`` and ``uppercase`` matter; mode is forced to phrase.
        clip_start_s: Clip start on the original clock; cue times are relative to it.
    """
    phrase = style.model_copy(update={"mode": "phrase"})
    # Resolution only affects styling, which SRT drops.
    return build_ass(transcript, phrase, 1080, 1920, clip_start_s).to_string("srt")
