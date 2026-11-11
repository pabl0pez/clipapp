"""Generate a synthetic OBS-like recording for manual testing.

Creates ``samples/sample_60s.mkv``: 60 s of 1080p60 test video with three audio tracks laid out
like ClipApp's OBS setup: track 1 mix, track 2 mic only (simulated voice: tones with silences),
track 3 game only (pink noise).

Run from ``engine/``: ``uv run python ../scripts/make_samples.py``
"""

import subprocess
import sys
from pathlib import Path

from clipapp_engine.config import ffmpeg_path

DURATION_S = 60
# Gaps (start, end) in seconds where the simulated voice is silent; each is 2-5 s long.
SILENCES = ((5, 8), (15, 20), (28, 30), (38, 43), (50, 52))
OUTPUT = Path(__file__).resolve().parent.parent / "samples" / "sample_60s.mkv"


def build_args(ffmpeg: Path, output: Path) -> list[str]:
    """Return the ffmpeg argument list that renders the sample file."""
    mute = "+".join(f"between(t,{a},{b})" for a, b in SILENCES)
    # Alternates 220/440 Hz every 0.5 s so the "voice" is not a flat tone.
    voice_expr = "0.5*sin(2*PI*(220+220*mod(floor(t*2),2))*t)"
    graph = (
        f"[1:a]volume=0:enable='{mute}',asplit[voice][voice_mix];"
        "[2:a]volume=0.5,asplit[game][game_mix];"
        "[voice_mix][game_mix]amix=inputs=2:normalize=0[mix]"
    )
    return [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-stats",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=1920x1080:rate=60:duration={DURATION_S}",
        "-f",
        "lavfi",
        "-i",
        f"aevalsrc=exprs='{voice_expr}':s=48000:d={DURATION_S}",
        "-f",
        "lavfi",
        "-i",
        f"anoisesrc=color=pink:sample_rate=48000:amplitude=0.3:duration={DURATION_S}",
        "-filter_complex",
        graph,
        "-map",
        "0:v",
        "-map",
        "[mix]",
        "-map",
        "[voice]",
        "-map",
        "[game]",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-metadata:s:a:0",
        "title=Mix",
        "-metadata:s:a:1",
        "title=Mic",
        "-metadata:s:a:2",
        "title=Game",
        str(output),
    ]


def main() -> int:
    """Render the sample file and return ffmpeg's exit code."""
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    code = subprocess.run(build_args(ffmpeg_path(), OUTPUT), check=False).returncode
    if code == 0:
        print(f"Wrote {OUTPUT}")
    return code


if __name__ == "__main__":
    sys.exit(main())
