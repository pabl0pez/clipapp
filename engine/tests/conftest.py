"""Session fixtures: synthetic media generated with FFmpeg lavfi."""

import shutil
import subprocess
from pathlib import Path

import pytest

from clipapp_engine.config import ffmpeg_path

DURATION_S = 20
MIC_SILENCES = ((3, 5), (12, 15))


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        [str(ffmpeg_path()), "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        capture_output=True,
    )


def _video_input(seconds: int) -> list[str]:
    return ["-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={seconds}"]


X264 = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def av_sample(media_dir: Path) -> Path:
    """20 s 640x360@30 MKV with tracks Mix, Mic (440 Hz, silent 3-5 s and 12-15 s), Game."""
    out = media_dir / "sample.mkv"
    mute = "+".join(f"between(t,{a},{b})" for a, b in MIC_SILENCES)
    graph = (
        f"[1:a]volume=0:enable='{mute}',asplit[mic][mic_mix];"
        "[2:a]asplit[game][game_mix];"
        "[mic_mix][game_mix]amix=inputs=2:normalize=0[mix]"
    )
    _ffmpeg(
        *_video_input(DURATION_S),
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=48000:duration={DURATION_S}",
        "-f",
        "lavfi",
        "-i",
        f"anoisesrc=color=pink:amplitude=0.05:sample_rate=48000:duration={DURATION_S}",
        "-filter_complex",
        graph,
        "-map",
        "0:v",
        "-map",
        "[mix]",
        "-map",
        "[mic]",
        "-map",
        "[game]",
        *X264,
        "-c:a",
        "aac",
        "-metadata:s:a:0",
        "title=Mix",
        "-metadata:s:a:1",
        "title=Mic",
        "-metadata:s:a:2",
        "title=Game",
        str(out),
    )
    return out


@pytest.fixture(scope="session")
def video_no_audio(media_dir: Path) -> Path:
    out = media_dir / "no_audio.mp4"
    _ffmpeg(*_video_input(2), *X264, str(out))
    return out


@pytest.fixture(scope="session")
def audio_only(media_dir: Path) -> Path:
    out = media_dir / "audio_only.m4a"
    _ffmpeg("-f", "lavfi", "-i", "sine=duration=2", "-c:a", "aac", str(out))
    return out


@pytest.fixture(scope="session")
def accented_sample(av_sample: Path, media_dir: Path) -> Path:
    folder = media_dir / "Grabación de ñandú"
    folder.mkdir()
    return Path(shutil.copy(av_sample, folder / "clip ó.mkv"))
