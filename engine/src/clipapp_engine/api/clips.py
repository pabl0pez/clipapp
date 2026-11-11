"""Routes for clips, their transcripts and the subtitle styles."""

import json
from typing import Any

from fastapi import APIRouter, Depends, Response, status

from clipapp_engine.api.deps import DB, Hub, Jobs, require_token
from clipapp_engine.api.schemas import (
    Clip,
    ClipFields,
    ClipPatch,
    Job,
    Style,
    StylePatch,
    TranscriptEdit,
    TranscriptOut,
)
from clipapp_engine.api.service import ConflictError, NotFoundError, clip_row, media_row, touch
from clipapp_engine.db.database import STYLE_FIELDS, Database, insert_style
from clipapp_engine.export.clip import validate_range
from clipapp_engine.subtitles.style import SubtitleStyle

router = APIRouter(dependencies=[Depends(require_token)])

CLIP_FIELDS = tuple(ClipFields.model_fields)


def _check_clip(db: Database, project_id: int, clip: ClipFields) -> None:
    """Validate a clip's range against the video and its style reference.

    Raises:
        ValueError: bad range or unknown style.
    """
    validate_range(clip.start_s, clip.end_s, media_row(db, project_id)["duration_s"])
    if clip.style_id is not None and not db.one(
        "SELECT 1 FROM subtitle_styles WHERE id = ?", (clip.style_id,)
    ):
        raise ValueError(f"style {clip.style_id} not found")


@router.get("/projects/{project_id}/clips", tags=["clips"])
def list_clips(project_id: int, db: DB) -> list[Clip]:
    """List a project's clips in time order."""
    media_row(db, project_id)
    rows = db.all("SELECT * FROM clips WHERE project_id = ? ORDER BY start_s", (project_id,))
    return [Clip.model_validate(r) for r in rows]


@router.post("/projects/{project_id}/clips", status_code=status.HTTP_201_CREATED, tags=["clips"])
def create_clip(project_id: int, body: ClipFields, db: DB, hub: Hub) -> Clip:
    """Create a clip (15-60 s inside the video)."""
    _check_clip(db, project_id, body)
    cols = ", ".join(CLIP_FIELDS)
    cur = db.execute(
        f"INSERT INTO clips (project_id, {cols}) VALUES (?{', ?' * len(CLIP_FIELDS)})",
        (project_id, *body.model_dump().values()),
    )
    touch(db, project_id)
    hub.publish({"type": "project_updated", "id": project_id})
    return Clip.model_validate(clip_row(db, int(cur.lastrowid or 0)))


@router.get("/clips/{clip_id}", tags=["clips"])
def get_clip(clip_id: int, db: DB) -> Clip:
    """Return one clip."""
    return Clip.model_validate(clip_row(db, clip_id))


@router.patch("/clips/{clip_id}", tags=["clips"])
def patch_clip(clip_id: int, body: ClipPatch, db: DB, hub: Hub) -> Clip:
    """Edit a clip; moving its start or end marks its transcript stale."""
    row = clip_row(db, clip_id)
    changes = body.model_dump(exclude_unset=True)
    merged = ClipFields.model_validate({**row, **changes})
    _check_clip(db, row["project_id"], merged)
    with db.transaction():
        db.execute(
            f"UPDATE clips SET {', '.join(f'{c} = ?' for c in CLIP_FIELDS)} WHERE id = ?",
            (*merged.model_dump().values(), clip_id),
        )
        if (merged.start_s, merged.end_s) != (row["start_s"], row["end_s"]):
            db.execute("UPDATE transcripts SET stale = 1 WHERE clip_id = ?", (clip_id,))
        touch(db, row["project_id"])
    hub.publish({"type": "project_updated", "id": row["project_id"]})
    return Clip.model_validate(clip_row(db, clip_id))


@router.delete("/clips/{clip_id}", status_code=204, tags=["clips"])
def delete_clip(clip_id: int, db: DB, hub: Hub) -> Response:
    """Delete a clip and its transcript."""
    row = clip_row(db, clip_id)
    db.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    touch(db, row["project_id"])
    hub.publish({"type": "project_updated", "id": row["project_id"]})
    return Response(status_code=204)


def _transcript(db: Database, clip_id: int) -> TranscriptOut:
    clip_row(db, clip_id)
    row = db.one("SELECT * FROM transcripts WHERE clip_id = ?", (clip_id,))
    if row is None:
        raise NotFoundError(f"clip {clip_id} has no transcript")
    return TranscriptOut.model_validate({**row, "words": json.loads(row["words"])})


@router.post(
    "/clips/{clip_id}/transcript", status_code=status.HTTP_202_ACCEPTED, tags=["transcripts"]
)
def transcribe_clip(clip_id: int, db: DB, jobs: Jobs) -> Job:
    """Queue the transcription of a clip (replaces any previous transcript)."""
    clip_row(db, clip_id)
    job = jobs.get(jobs.submit("transcribe", clip_id))
    return Job.model_validate(job)


@router.get("/clips/{clip_id}/transcript", tags=["transcripts"])
def get_transcript(clip_id: int, db: DB) -> TranscriptOut:
    """Return a clip's transcript."""
    return _transcript(db, clip_id)


@router.put("/clips/{clip_id}/transcript", tags=["transcripts"])
def put_transcript(clip_id: int, body: TranscriptEdit, db: DB, hub: Hub) -> TranscriptOut:
    """Save corrected words (the transcript becomes ``edited`` and no longer stale)."""
    current = _transcript(db, clip_id)
    words = json.dumps([w.model_dump() for w in body.words])
    db.execute(
        "UPDATE transcripts SET words = ?, edited = 1, stale = 0 WHERE clip_id = ?",
        (words, clip_id),
    )
    hub.publish({"type": "project_updated", "id": clip_row(db, clip_id)["project_id"]})
    return current.model_copy(update={"words": body.words, "edited": True, "stale": False})


def _style(db: Database, style_id: int) -> dict[str, Any]:
    row = db.one("SELECT * FROM subtitle_styles WHERE id = ?", (style_id,))
    if row is None:
        raise NotFoundError(f"style {style_id} not found")
    return row


@router.get("/styles", tags=["styles"])
def list_styles(db: DB) -> list[Style]:
    """List the subtitle styles (factory ones first)."""
    rows = db.all("SELECT * FROM subtitle_styles ORDER BY builtin DESC, id")
    return [Style.model_validate(r) for r in rows]


@router.post("/styles", status_code=status.HTTP_201_CREATED, tags=["styles"])
def create_style(body: SubtitleStyle, db: DB) -> Style:
    """Create a custom style."""
    return Style.model_validate(_style(db, insert_style(db, body)))


@router.patch("/styles/{style_id}", tags=["styles"])
def patch_style(style_id: int, body: StylePatch, db: DB) -> Style:
    """Edit a style (factory styles too; they only cannot be deleted)."""
    row = _style(db, style_id)
    merged = SubtitleStyle.model_validate({**row, **body.model_dump(exclude_unset=True)})
    db.execute(
        f"UPDATE subtitle_styles SET {', '.join(f'{f} = ?' for f in STYLE_FIELDS)} WHERE id = ?",
        (*(getattr(merged, f) for f in STYLE_FIELDS), style_id),
    )
    return Style.model_validate(_style(db, style_id))


@router.delete("/styles/{style_id}", status_code=204, tags=["styles"])
def delete_style(style_id: int, db: DB) -> Response:
    """Delete a custom style; clips using it lose their subtitles style."""
    if _style(db, style_id)["builtin"]:
        raise ConflictError("factory styles cannot be deleted")
    db.execute("DELETE FROM subtitle_styles WHERE id = ?", (style_id,))
    return Response(status_code=204)
