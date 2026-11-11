"""Engine operations behind the API: row loading, silence analysis and the job handlers.

Everything heavy reuses the Stage 1-7 modules; this layer only moves data between them and
the database.
"""

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from clipapp_engine.api.schemas import Settings, Silence, SilenceRange
from clipapp_engine.db.database import STYLE_FIELDS, Database
from clipapp_engine.export.clip import ClipOptions
from clipapp_engine.export.full import RenderOptions, render_without_silences
from clipapp_engine.export.pipeline import export_clip, output_path
from clipapp_engine.jobs.queue import Handler, JobCancelledError, ProgressFn
from clipapp_engine.media.prepare import prepare
from clipapp_engine.silence.detect import detect_silences
from clipapp_engine.silence.ranges import Range, compute_keep_ranges, stats
from clipapp_engine.silence.settings import SilenceSettings
from clipapp_engine.subtitles.style import SubtitleStyle
from clipapp_engine.transcribe.cache import transcript_cache_path
from clipapp_engine.transcribe.device import pick_device
from clipapp_engine.transcribe.transcript import Transcript
from clipapp_engine.transcribe.whisper import transcribe_range

Emit = Callable[[dict[str, Any]], None]
EDGE_DIGITS = 3  # silences are matched across recomputes by their bounds in milliseconds


class NotFoundError(LookupError):
    """A referenced row does not exist."""


class ConflictError(RuntimeError):
    """The request is valid but cannot run in the current state."""


def media_row(db: Database, project_id: int) -> dict[str, Any]:
    """Return the project's ``media_assets`` row.

    Raises:
        NotFoundError: no such project.
    """
    row = db.one("SELECT * FROM media_assets WHERE project_id = ?", (project_id,))
    if row is None:
        raise NotFoundError(f"project {project_id} not found")
    return row


def clip_row(db: Database, clip_id: int) -> dict[str, Any]:
    """Return a ``clips`` row.

    Raises:
        NotFoundError: no such clip.
    """
    row = db.one("SELECT * FROM clips WHERE id = ?", (clip_id,))
    if row is None:
        raise NotFoundError(f"clip {clip_id} not found")
    return row


def load_settings(db: Database) -> Settings:
    """Return the app settings."""
    row = db.one("SELECT * FROM app_settings WHERE id = 1")
    assert row is not None  # seeded by migration 1
    return Settings.model_validate({**row, "glossary": json.loads(row["glossary"])})


def load_style(db: Database, style_id: int | None) -> SubtitleStyle | None:
    """Return a stored style, or None for ``style_id=None`` or a deleted one."""
    if style_id is None:
        return None
    row = db.one("SELECT * FROM subtitle_styles WHERE id = ?", (style_id,))
    return SubtitleStyle.model_validate({f: row[f] for f in STYLE_FIELDS}) if row else None


def touch(db: Database, project_id: int) -> None:
    """Refresh the project's ``updated_at``."""
    db.execute(
        "UPDATE projects SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
        (project_id,),
    )


def mic_wav(media: dict[str, Any]) -> Path:
    """Return the cached mic WAV of a prepared project.

    Raises:
        ConflictError: the project is not prepared yet (no WAV).
    """
    wav = Path(media["cache_dir"]) / f"track{media['mic_track']}.wav"
    if not wav.is_file():
        raise ConflictError("the project is not prepared yet")
    return wav


def _gaps(duration_s: float, keep: list[Range]) -> list[Range]:
    """Return the stretches of ``[0, duration_s]`` not covered by the sorted ``keep``."""
    bounds = [0.0, *(t for r in keep for t in r), duration_s]
    return [(a, b) for a, b in zip(bounds[::2], bounds[1::2], strict=True) if b - a > 1e-9]


def _edge_key(start_s: float, end_s: float) -> tuple[float, float]:
    return round(start_s, EDGE_DIGITS), round(end_s, EDGE_DIGITS)


def silence_state(db: Database, project_id: int) -> Silence:
    """Return settings, ranges and statistics (counting only enabled ranges)."""
    media = media_row(db, project_id)
    settings = db.one("SELECT * FROM silence_settings WHERE project_id = ?", (project_id,))
    ranges = db.all(
        "SELECT id, start_s, end_s, enabled FROM silence_ranges WHERE project_id = ? "
        "ORDER BY start_s",
        (project_id,),
    )
    duration = media["duration_s"]
    return Silence(
        settings=SilenceSettings.model_validate(settings),
        ranges=[SilenceRange.model_validate(r) for r in ranges],
        stats=stats(duration, keep_ranges(duration, ranges)),
    )


def keep_ranges(duration_s: float, ranges: list[dict[str, Any]]) -> list[Range]:
    """Return the ranges the full export keeps: everything but the enabled silences."""
    cuts = [(r["start_s"], r["end_s"]) for r in ranges if r["enabled"]]
    return compute_keep_ranges(duration_s, cuts, 0.0, 0.0)


def recompute_silences(
    db: Database,
    project_id: int,
    settings: SilenceSettings | None = None,
    cancel_event: threading.Event | None = None,
) -> Silence:
    """Detect silences again and replace the project's ranges.

    ``settings`` are stored first (None reuses the saved ones); detection runs on the cached
    mic WAV, and ranges whose bounds did not change keep their ``enabled`` flag.

    Raises:
        NotFoundError: no such project.
        ConflictError: the project is not prepared.
        FFmpegError: detection failed.
    """
    media = media_row(db, project_id)
    wav = mic_wav(media)
    if settings is None:
        row = db.one("SELECT * FROM silence_settings WHERE project_id = ?", (project_id,))
        settings = SilenceSettings.model_validate(row)
    found = detect_silences(wav, settings.threshold_db, settings.min_silence_s, None, cancel_event)
    duration = media["duration_s"]
    keep = compute_keep_ranges(duration, found, settings.padding_s, settings.min_keep_s)
    with db.transaction():
        old = db.all(
            "SELECT start_s, end_s, enabled FROM silence_ranges WHERE project_id = ?",
            (project_id,),
        )
        enabled = {_edge_key(r["start_s"], r["end_s"]): r["enabled"] for r in old}
        db.execute(
            "UPDATE silence_settings SET threshold_db = ?, min_silence_s = ?, padding_s = ?, "
            "min_keep_s = ? WHERE project_id = ?",
            (*settings.model_dump().values(), project_id),
        )
        db.execute("DELETE FROM silence_ranges WHERE project_id = ?", (project_id,))
        for s, e in _gaps(duration, keep):
            db.execute(
                "INSERT INTO silence_ranges (project_id, start_s, end_s, enabled) "
                "VALUES (?, ?, ?, ?)",
                (project_id, s, e, enabled.get(_edge_key(s, e), 1)),
            )
        touch(db, project_id)
    return silence_state(db, project_id)


def store_transcript(db: Database, clip_id: int, transcript: Transcript, cache_key: str) -> None:
    """Insert or replace a clip's machine transcript (not edited, not stale)."""
    words = json.dumps([w.model_dump() for w in transcript.words])
    db.execute(
        "INSERT OR REPLACE INTO transcripts (clip_id, model, language, words, cache_key) "
        "VALUES (?, ?, ?, ?, ?)",
        (clip_id, transcript.model, transcript.language, words, cache_key),
    )


def _transcribe(db: Database, clip: dict[str, Any], cancel: threading.Event) -> Transcript:
    """Transcribe a clip's mic range with the configured model and store the result."""
    media = media_row(db, clip["project_id"])
    settings = load_settings(db)
    choice = pick_device(settings.whisper_model, settings.device)
    transcript = transcribe_range(
        Path(media["path"]),
        media["mic_track"],
        clip["start_s"],
        clip["end_s"],
        settings.glossary,
        choice,
    )
    if cancel.is_set():  # Whisper cannot be interrupted; drop the result instead
        raise JobCancelledError
    key = transcript_cache_path(
        media["file_hash"],
        media["mic_track"],
        clip["start_s"],
        clip["end_s"],
        transcript.model,
        settings.glossary.digest(),
    ).stem
    store_transcript(db, clip["id"], transcript, key)
    return transcript


def make_handlers(db: Database, emit: Emit) -> dict[str, Handler]:
    """Build the job handlers (prepare, full, clip, transcribe) bound to ``db`` and ``emit``."""

    def updated(project_id: int) -> None:
        emit({"type": "project_updated", "id": project_id})

    def set_prepare(project_id: int, status: str) -> None:
        db.execute(
            "UPDATE media_assets SET prepare_status = ? WHERE project_id = ?",
            (status, project_id),
        )
        touch(db, project_id)
        updated(project_id)

    def run_prepare(project_id: int, progress: ProgressFn, cancel: threading.Event) -> None:
        media = media_row(db, project_id)
        set_prepare(project_id, "running")
        try:
            prepare(
                Path(media["path"]), media["mic_track"], lambda _s, p: progress(p), False, cancel
            )
            recompute_silences(db, project_id, None, cancel)
        except BaseException:
            set_prepare(project_id, "failed")
            raise
        set_prepare(project_id, "done")

    def run_full(project_id: int, progress: ProgressFn, cancel: threading.Event) -> str:
        media = media_row(db, project_id)
        settings = load_settings(db)
        ranges = db.all("SELECT * FROM silence_ranges WHERE project_id = ?", (project_id,))
        video = Path(media["path"])
        out = output_path(Path(settings.output_dir), video, "sin silencios", "16:9")
        out.parent.mkdir(parents=True, exist_ok=True)
        options = RenderOptions(
            mic_track=media["mic_track"],
            game_track=media["game_track"],
            mic_volume=settings.mic_volume,
            game_volume=settings.game_volume,
            encoder=settings.encoder,
        )
        keep = keep_ranges(media["duration_s"], ranges)
        render_without_silences(video, keep, options, out, progress, cancel)
        return str(out)

    def run_clip(clip_id: int, progress: ProgressFn, cancel: threading.Event) -> str:
        clip = clip_row(db, clip_id)
        media = media_row(db, clip["project_id"])
        settings = load_settings(db)
        style = load_style(db, clip["style_id"])
        transcript = None
        if style is not None:
            row = db.one("SELECT * FROM transcripts WHERE clip_id = ?", (clip_id,))
            if row is not None and not row["stale"]:
                transcript = Transcript.model_validate({**row, "words": json.loads(row["words"])})
            else:
                transcript = _transcribe(db, clip, cancel)
        options = ClipOptions(
            start_s=clip["start_s"],
            end_s=clip["end_s"],
            aspect=clip["aspect"],
            crop_mode=clip["crop_mode"],
            crop_x=clip["crop_x"],
            style=style,
            normalize=bool(clip["normalize"]),
            mic_track=media["mic_track"],
            game_track=media["game_track"],
            mic_volume=settings.mic_volume,
            game_volume=settings.game_volume,
            encoder=settings.encoder,
        )
        out = export_clip(
            Path(media["path"]),
            options,
            Path(settings.output_dir),
            clip["name"],
            settings.glossary,
            on_progress=progress,
            cancel_event=cancel,
            transcript=transcript,
        )
        return str(out)

    def run_transcribe(clip_id: int, progress: ProgressFn, cancel: threading.Event) -> None:
        clip = clip_row(db, clip_id)
        _transcribe(db, clip, cancel)
        progress(1.0)
        updated(clip["project_id"])

    return {
        "prepare": run_prepare,
        "full": run_full,
        "clip": run_clip,
        "transcribe": run_transcribe,
    }
