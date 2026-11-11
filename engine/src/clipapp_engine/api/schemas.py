"""Request and response models of the local API (they also define the OpenAPI schema)."""

from typing import Literal

from pydantic import BaseModel, Field

from clipapp_engine.export.clip import Aspect
from clipapp_engine.jobs.queue import JobKind, JobStatus
from clipapp_engine.media.probe import AudioTrack
from clipapp_engine.silence.ranges import SilenceStats
from clipapp_engine.silence.settings import SilenceSettings
from clipapp_engine.subtitles.style import Mode, SubtitleStyle
from clipapp_engine.transcribe.glossary import Glossary
from clipapp_engine.transcribe.transcript import Word

PrepareStatus = Literal["pending", "running", "done", "failed"]
CropMode = Literal["crop", "blur"]


class Health(BaseModel):
    """Liveness answer."""

    status: Literal["ok"] = "ok"
    version: str


class Media(BaseModel):
    """The project's recording and its prepared cache."""

    path: str
    file_hash: str
    duration_s: float
    fps: float
    width: int
    height: int
    audio_tracks: list[AudioTrack]
    mic_track: int
    game_track: int
    cache_dir: str
    prepare_status: PrepareStatus


class Project(BaseModel):
    """A recording being edited."""

    id: int
    name: str
    created_at: str
    updated_at: str
    media: Media


class ProjectCreate(BaseModel):
    """Import a recording; ``name`` defaults to the file name."""

    path: str
    name: str | None = None


class ProjectPatch(BaseModel):
    """Editable project fields (omitted ones are unchanged)."""

    name: str | None = Field(default=None, min_length=1)
    mic_track: int | None = Field(default=None, ge=0)
    game_track: int | None = Field(default=None, ge=0)


class Relink(BaseModel):
    """New location of a moved recording."""

    path: str


class SilenceRange(BaseModel):
    """A stretch removed by the full export unless disabled."""

    id: int
    start_s: float
    end_s: float
    enabled: bool


class SilenceRangePatch(BaseModel):
    """Toggle one silence."""

    enabled: bool


class Silence(BaseModel):
    """Silence settings, detected ranges and the resulting cut statistics."""

    settings: SilenceSettings
    ranges: list[SilenceRange]
    stats: SilenceStats


class ClipFields(BaseModel):
    """Clip fields shared by create and response."""

    name: str = Field(min_length=1)
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    aspect: Aspect = "9:16"
    crop_mode: CropMode = "crop"
    crop_x: float = Field(default=0.5, ge=0, le=1)
    style_id: int | None = None
    normalize: bool = True


class Clip(ClipFields):
    """A clip of a project."""

    id: int
    project_id: int
    created_at: str


class ClipPatch(BaseModel):
    """Editable clip fields (omitted ones are unchanged; ``style_id: null`` removes subs)."""

    name: str | None = Field(default=None, min_length=1)
    start_s: float | None = Field(default=None, ge=0)
    end_s: float | None = Field(default=None, ge=0)
    aspect: Aspect | None = None
    crop_mode: CropMode | None = None
    crop_x: float | None = Field(default=None, ge=0, le=1)
    style_id: int | None = None
    normalize: bool | None = None


class TranscriptOut(BaseModel):
    """A clip's words; ``stale`` means the clip range changed since it was made."""

    clip_id: int
    model: str
    language: str
    words: list[Word]
    cache_key: str
    edited: bool
    stale: bool


class TranscriptEdit(BaseModel):
    """Corrected words of a clip."""

    words: list[Word]


class Style(SubtitleStyle):
    """A stored subtitle style; ``builtin`` ones cannot be deleted."""

    id: int
    builtin: bool


class StylePatch(BaseModel):
    """Editable style fields (omitted ones are unchanged)."""

    name: str | None = None
    font: str | None = None
    size: float | None = None
    primary_color: str | None = None
    highlight_color: str | None = None
    outline_color: str | None = None
    outline: float | None = None
    shadow: float | None = None
    position_y: float | None = None
    mode: Mode | None = None
    max_chars: int | None = None
    max_lines: int | None = None
    uppercase: bool | None = None


class Job(BaseModel):
    """A background job (prepare, export or transcription)."""

    id: int
    kind: JobKind
    target_id: int
    status: JobStatus
    progress: float
    output_path: str | None
    error: str | None
    created_at: str
    finished_at: str | None


class ExportRequest(BaseModel):
    """Export a project without silences (``full``, target = project) or a ``clip``."""

    kind: Literal["full", "clip"]
    target_id: int


class Settings(BaseModel):
    """App-wide settings; None values mean auto-detect."""

    output_dir: str = Field(min_length=1)
    encoder: str | None = None
    whisper_model: str | None = None
    device: Literal["cuda", "cpu"] | None = None
    mic_volume: float = Field(default=1.0, ge=0)
    game_volume: float = Field(default=0.6, ge=0)
    glossary: Glossary = Glossary()
