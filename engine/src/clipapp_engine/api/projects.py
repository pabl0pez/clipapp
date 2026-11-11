"""Routes for projects, their prepared media files and silence editing."""

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse

from clipapp_engine.api.deps import DB, Hub, Jobs, require_media_token, require_token
from clipapp_engine.api.schemas import (
    Media,
    Project,
    ProjectCreate,
    ProjectPatch,
    Relink,
    Silence,
    SilenceRange,
    SilenceRangePatch,
)
from clipapp_engine.api.service import (
    ConflictError,
    NotFoundError,
    media_row,
    mic_wav,
    recompute_silences,
    silence_state,
    touch,
)
from clipapp_engine.config import data_dir
from clipapp_engine.db.database import Database
from clipapp_engine.jobs.queue import JobQueue
from clipapp_engine.media.hashing import file_hash
from clipapp_engine.media.probe import probe
from clipapp_engine.silence.noise import ThresholdSuggestion, suggest_threshold
from clipapp_engine.silence.settings import SilenceSettings

router = APIRouter(dependencies=[Depends(require_token)])
media_router = APIRouter(dependencies=[Depends(require_media_token)])

MEDIA_FILES = {
    "proxy": "proxy.mp4",
    "waveform": "waveform.json",
    "thumbnails": "sprite.jpg",
    "thumbnails/index": "thumbnails.json",
}


def project_out(db: Database, project_id: int) -> Project:
    """Return the full project model.

    Raises:
        NotFoundError: no such project.
    """
    project = db.one("SELECT * FROM projects WHERE id = ?", (project_id,))
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    media = media_row(db, project_id)
    media["audio_tracks"] = json.loads(media["audio_tracks"])
    return Project.model_validate({**project, "media": Media.model_validate(media)})


@router.post("/projects", status_code=status.HTTP_201_CREATED, tags=["projects"])
def create_project(body: ProjectCreate, db: DB, jobs: Jobs, hub: Hub) -> Project:
    """Probe a recording, create its project and queue its preparation."""
    path = Path(body.path)
    info = probe(path)
    digest = file_hash(path)
    tracks = len(info.audio_tracks)
    with db.transaction():
        cur = db.execute("INSERT INTO projects (name) VALUES (?)", (body.name or path.stem,))
        project_id = int(cur.lastrowid or 0)
        db.execute(
            "INSERT INTO media_assets (project_id, path, file_hash, duration_s, fps, width, "
            "height, audio_tracks, mic_track, game_track, cache_dir) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                str(path.resolve()),
                digest,
                info.duration_s,
                float(info.fps),
                info.width,
                info.height,
                json.dumps([t.model_dump() for t in info.audio_tracks]),
                min(1, tracks - 1),  # OBS layout: track 2 mic, track 3 game
                min(2, tracks - 1),
                str(data_dir() / "cache" / digest),
            ),
        )
        defaults = SilenceSettings()
        db.execute(
            "INSERT INTO silence_settings VALUES (?, ?, ?, ?, ?)",
            (project_id, *defaults.model_dump().values()),
        )
    jobs.submit("prepare", project_id)
    hub.publish({"type": "project_updated", "id": project_id})
    return project_out(db, project_id)


@router.get("/projects", tags=["projects"])
def list_projects(db: DB) -> list[Project]:
    """List every project, most recently updated first."""
    rows = db.all("SELECT id FROM projects ORDER BY updated_at DESC, id DESC")
    return [project_out(db, r["id"]) for r in rows]


@router.get("/projects/{project_id}", tags=["projects"])
def get_project(project_id: int, db: DB) -> Project:
    """Return one project."""
    return project_out(db, project_id)


def _project_jobs(db: Database, project_id: int) -> list[dict[str, Any]]:
    return db.all(
        "SELECT id FROM export_jobs WHERE status IN ('queued', 'running') AND ("
        "(kind IN ('prepare', 'full') AND target_id = ?) OR "
        "(kind IN ('clip', 'transcribe') AND target_id IN "
        "(SELECT id FROM clips WHERE project_id = ?)))",
        (project_id, project_id),
    )


@router.delete("/projects/{project_id}", status_code=204, tags=["projects"])
def delete_project(project_id: int, db: DB, jobs: Jobs, hub: Hub) -> Response:
    """Delete a project's data and cache (the original video is never touched)."""
    media = media_row(db, project_id)
    for job in _project_jobs(db, project_id):
        jobs.cancel(job["id"])
    db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    cache = Path(media["cache_dir"])
    shared = db.one("SELECT 1 FROM media_assets WHERE cache_dir = ?", (str(cache),))
    # The cache is only removed when it is ours: inside <data>/cache and used by no other project.
    if shared is None and cache.parent == data_dir() / "cache":
        shutil.rmtree(cache, ignore_errors=True)
    hub.publish({"type": "project_updated", "id": project_id})
    return Response(status_code=204)


def _requeue_prepare(db: Database, jobs: JobQueue, project_id: int) -> None:
    db.execute(
        "UPDATE media_assets SET prepare_status = 'pending' WHERE project_id = ?", (project_id,)
    )
    jobs.submit("prepare", project_id)


@router.patch("/projects/{project_id}", tags=["projects"])
def patch_project(project_id: int, body: ProjectPatch, db: DB, jobs: Jobs, hub: Hub) -> Project:
    """Rename a project or change its tracks; a new mic track rebuilds the prepared files."""
    media = media_row(db, project_id)
    tracks = len(json.loads(media["audio_tracks"]))
    for track in (body.mic_track, body.game_track):
        if track is not None and track >= tracks:
            raise ValueError(f"audio track {track} does not exist (0..{tracks - 1})")
    mic_changed = body.mic_track is not None and body.mic_track != media["mic_track"]
    with db.transaction():
        if body.name is not None:
            db.execute("UPDATE projects SET name = ? WHERE id = ?", (body.name, project_id))
        for col in ("mic_track", "game_track"):
            value = getattr(body, col)
            if value is not None:
                db.execute(
                    f"UPDATE media_assets SET {col} = ? WHERE project_id = ?",
                    (value, project_id),
                )
        if mic_changed:
            db.execute(
                "UPDATE transcripts SET stale = 1 WHERE clip_id IN "
                "(SELECT id FROM clips WHERE project_id = ?)",
                (project_id,),
            )
        touch(db, project_id)
    if mic_changed:
        _requeue_prepare(db, jobs, project_id)
    hub.publish({"type": "project_updated", "id": project_id})
    return project_out(db, project_id)


@router.post("/projects/{project_id}/relink", tags=["projects"])
def relink_project(project_id: int, body: Relink, db: DB, jobs: Jobs, hub: Hub) -> Project:
    """Point a project at its moved recording; the file must have the same fingerprint."""
    media = media_row(db, project_id)
    path = Path(body.path)
    if not path.is_file():
        raise ValueError(f"file not found: {path}")
    if file_hash(path) != media["file_hash"]:
        raise ConflictError("this file is not the project's original recording")
    db.execute(
        "UPDATE media_assets SET path = ? WHERE project_id = ?", (str(path.resolve()), project_id)
    )
    if media["prepare_status"] == "failed":
        _requeue_prepare(db, jobs, project_id)
    touch(db, project_id)
    hub.publish({"type": "project_updated", "id": project_id})
    return project_out(db, project_id)


def _media_file(db: Database, project_id: int, kind: str) -> FileResponse:
    file = Path(media_row(db, project_id)["cache_dir"]) / MEDIA_FILES[kind]
    if not file.is_file():
        raise HTTPException(404, f"{kind} not ready")
    return FileResponse(file)


@media_router.get("/projects/{project_id}/media/proxy", tags=["media"])
def get_proxy(project_id: int, db: DB) -> FileResponse:
    """Stream the 720p proxy (supports HTTP Range)."""
    return _media_file(db, project_id, "proxy")


@media_router.get("/projects/{project_id}/media/waveform", tags=["media"])
def get_waveform(project_id: int, db: DB) -> FileResponse:
    """Return the waveform peaks JSON (audiowaveform v2 format)."""
    return _media_file(db, project_id, "waveform")


@media_router.get("/projects/{project_id}/media/thumbnails", tags=["media"])
def get_thumbnails(project_id: int, db: DB) -> FileResponse:
    """Return the thumbnail sprite (JPG grid)."""
    return _media_file(db, project_id, "thumbnails")


@media_router.get("/projects/{project_id}/media/thumbnails/index", tags=["media"])
def get_thumbnails_index(project_id: int, db: DB) -> FileResponse:
    """Return the sprite layout JSON (interval, size, columns, rows, count)."""
    return _media_file(db, project_id, "thumbnails/index")


@router.get("/projects/{project_id}/silence", tags=["silence"])
def get_silence(project_id: int, db: DB) -> Silence:
    """Return silence settings, ranges and statistics."""
    return silence_state(db, project_id)


@router.put("/projects/{project_id}/silence", tags=["silence"])
def put_silence(project_id: int, body: SilenceSettings, db: DB, hub: Hub) -> Silence:
    """Save the settings and recompute the silences now (keeps unchanged ranges' toggles)."""
    result = recompute_silences(db, project_id, body)
    hub.publish({"type": "project_updated", "id": project_id})
    return result


@router.get("/projects/{project_id}/silence/suggest", tags=["silence"])
def suggest_silence(project_id: int, db: DB) -> ThresholdSuggestion:
    """Suggest a threshold from the mic track's noise floor."""
    return suggest_threshold(mic_wav(media_row(db, project_id)))


@router.patch("/silence-ranges/{range_id}", tags=["silence"])
def patch_silence_range(range_id: int, body: SilenceRangePatch, db: DB, hub: Hub) -> SilenceRange:
    """Enable or disable one silence."""
    row = db.one("SELECT * FROM silence_ranges WHERE id = ?", (range_id,))
    if row is None:
        raise NotFoundError(f"silence range {range_id} not found")
    db.execute("UPDATE silence_ranges SET enabled = ? WHERE id = ?", (int(body.enabled), range_id))
    touch(db, row["project_id"])
    hub.publish({"type": "project_updated", "id": row["project_id"]})
    return SilenceRange.model_validate({**row, "enabled": body.enabled})
