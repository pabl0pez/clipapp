import json
from pathlib import Path

from typer.testing import CliRunner

from clipapp_engine.cli import app


def test_probe_json(accented_sample: Path) -> None:
    result = CliRunner().invoke(app, ["probe", str(accented_sample), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["fps"] == "30"
    assert [t["title"] for t in data["audio_tracks"]] == ["Mix", "Mic", "Game"]


def test_probe_table(av_sample: Path) -> None:
    result = CliRunner().invoke(app, ["probe", str(av_sample)])
    assert result.exit_code == 0, result.output
    for title in ("Mix", "Mic", "Game"):
        assert title in result.stdout


def test_probe_missing_file(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["probe", str(tmp_path / "nope.mkv")])
    assert result.exit_code == 1
    assert "File not found" in result.output


def test_silence_json(av_sample: Path) -> None:
    result = CliRunner().invoke(app, ["silence", str(av_sample), "--track", "1", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert len(data["silences"]) == 2
    assert len(data["keep_ranges"]) == 3
    assert data["stats"]["cuts"] == 2
    assert data["settings"]["threshold_db"] == -35


def test_silence_suggest_table(av_sample: Path) -> None:
    result = CliRunner().invoke(app, ["silence", str(av_sample), "--track", "0", "--suggest"])
    assert result.exit_code == 0, result.output
    assert "Suggested threshold" in result.stdout
    assert "warning" in result.stdout


def test_silence_bad_track(av_sample: Path) -> None:
    result = CliRunner().invoke(app, ["silence", str(av_sample), "--track", "9"])
    assert result.exit_code == 1
    assert "not found" in result.output
