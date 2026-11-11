"""Routes for health, diagnostics, exports (jobs) and app settings."""

import json
from importlib.metadata import version
from typing import Any

from fastapi import APIRouter, Depends, status

from clipapp_engine.api.deps import DB, Jobs, require_token
from clipapp_engine.api.schemas import ExportRequest, Health, Job, Settings
from clipapp_engine.api.service import (
    ConflictError,
    NotFoundError,
    clip_row,
    load_settings,
    media_row,
)
from clipapp_engine.doctor import run_doctor
from clipapp_engine.media.encoders import save_encoder_cache

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/health", tags=["system"])
def health() -> Health:
    """Report that the engine is up."""
    return Health(version=version("clipapp-engine"))


@router.get("/system", tags=["system"])
def system() -> dict[str, Any]:
    """Run ``doctor``: FFmpeg, encoders, GPUs, CUDA, data dir and free disk (takes seconds)."""
    report = run_doctor()
    if report.ffmpeg_version != "not found":
        save_encoder_cache(report.ffmpeg_version, report.encoders)
    return report.to_dict()


def _job(jobs: Jobs, job_id: int) -> Job:
    row = jobs.get(job_id)
    if row is None:
        raise NotFoundError(f"job {job_id} not found")
    return Job.model_validate(row)


@router.post("/exports", status_code=status.HTTP_202_ACCEPTED, tags=["exports"])
def create_exports(body: ExportRequest | list[ExportRequest], db: DB, jobs: Jobs) -> list[Job]:
    """Queue one export or a batch; every target is checked before anything is queued."""
    requests = body if isinstance(body, list) else [body]
    for req in requests:
        if req.kind == "full":
            media_row(db, req.target_id)
        else:
            clip_row(db, req.target_id)
    return [_job(jobs, jobs.submit(req.kind, req.target_id)) for req in requests]


@router.get("/exports", tags=["exports"])
def list_exports(db: DB) -> list[Job]:
    """List every job (prepare, exports, transcriptions), newest first."""
    return [Job.model_validate(r) for r in db.all("SELECT * FROM export_jobs ORDER BY id DESC")]


@router.post("/exports/{job_id}/cancel", tags=["exports"])
def cancel_export(job_id: int, jobs: Jobs) -> Job:
    """Cancel a queued or running job."""
    _job(jobs, job_id)
    if not jobs.cancel(job_id):
        raise ConflictError("the job already finished")
    return _job(jobs, job_id)


@router.post("/exports/{job_id}/retry", tags=["exports"])
def retry_export(job_id: int, jobs: Jobs) -> Job:
    """Queue a failed or cancelled job again."""
    _job(jobs, job_id)
    if not jobs.retry(job_id):
        raise ConflictError("only failed or cancelled jobs can be retried")
    return _job(jobs, job_id)


@router.get("/settings", tags=["settings"])
def get_settings(db: DB) -> Settings:
    """Return the app settings."""
    return load_settings(db)


@router.put("/settings", tags=["settings"])
def put_settings(body: Settings, db: DB) -> Settings:
    """Replace the app settings."""
    db.execute(
        "UPDATE app_settings SET output_dir = ?, encoder = ?, whisper_model = ?, device = ?, "
        "mic_volume = ?, game_volume = ?, glossary = ? WHERE id = 1",
        (
            body.output_dir,
            body.encoder,
            body.whisper_model,
            body.device,
            body.mic_volume,
            body.game_volume,
            json.dumps(body.glossary.model_dump()),
        ),
    )
    return load_settings(db)
