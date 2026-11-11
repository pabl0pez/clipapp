"""Export one PNG frame per subtitle template and aspect, to check how they look.

Writes ``samples/previews/<style>_<aspect>.png`` using a sample Spanish phrase (with long words to
test the 9:16 edges) over a test pattern, or over ``--video`` if given.

Run from ``engine/``: ``uv run python ../scripts/preview_styles.py [--video file.mkv]``
"""

import argparse
import tempfile
from pathlib import Path

from clipapp_engine.media.runner import run_ffmpeg
from clipapp_engine.subtitles.ass import burn_filter, build_ass
from clipapp_engine.subtitles.style import TEMPLATES
from clipapp_engine.transcribe.transcript import Transcript, Word

OUTPUT = Path(__file__).resolve().parent.parent / "samples" / "previews"
PHRASE = "¿Viste eso? Increíblemente conseguimos ganar la última ronda espectacular"
WORD_S = 0.35
FRAME_AT_S = 1.45  # inside "Increíblemente", the second caption's first word
ASPECTS = {"9x16": (1080, 1920), "16x9": (1920, 1080)}


def sample_transcript() -> Transcript:
    """Return the sample phrase with evenly spaced word timings."""
    words = [
        Word(text=t, start_s=i * WORD_S, end_s=(i + 0.9) * WORD_S, probability=1.0)
        for i, t in enumerate(PHRASE.split())
    ]
    return Transcript(model="sample", language="es", words=words)


def background_args(video: Path | None, width: int, height: int) -> list[str]:
    """Return the ffmpeg input args for the background (a test pattern unless ``video``)."""
    if video is None:
        return ["-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=30:duration=5"]
    return ["-i", str(video)]


def main() -> None:
    """Render every template x aspect preview frame."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, help="Background video instead of a test pattern.")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    transcript = sample_transcript()
    with tempfile.TemporaryDirectory() as tmp:
        for key, style in TEMPLATES.items():
            for aspect, (width, height) in ASPECTS.items():
                ass = Path(tmp) / f"{key}_{aspect}.ass"
                build_ass(transcript, style, width, height, 0.0).save(str(ass))
                fit = (
                    f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},"
                )
                out = OUTPUT / f"{key}_{aspect}.png"
                run_ffmpeg(
                    [
                        *background_args(args.video, width, height),
                        "-vf",
                        fit + burn_filter(ass),
                        "-ss",
                        str(FRAME_AT_S),
                        "-frames:v",
                        "1",
                        str(out),
                    ],
                    0,
                )
                print(out)


if __name__ == "__main__":
    main()
