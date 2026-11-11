"""SQLite persistence: thread-safe connection, numbered migrations and factory data."""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from clipapp_engine.config import data_dir
from clipapp_engine.subtitles.style import TEMPLATES, SubtitleStyle

DB_NAME = "clipapp.db"
STYLE_FIELDS = tuple(SubtitleStyle.model_fields)

# Migration N (1-based) brings the schema from version N-1 to N. Never edit a shipped one.
MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE projects (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    );
    CREATE TABLE media_assets (
        project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        file_hash TEXT NOT NULL,
        duration_s REAL NOT NULL,
        fps REAL NOT NULL,
        width INTEGER NOT NULL,
        height INTEGER NOT NULL,
        audio_tracks TEXT NOT NULL,
        mic_track INTEGER NOT NULL,
        game_track INTEGER NOT NULL,
        cache_dir TEXT NOT NULL,
        prepare_status TEXT NOT NULL DEFAULT 'pending'
    );
    CREATE TABLE silence_settings (
        project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
        threshold_db REAL NOT NULL,
        min_silence_s REAL NOT NULL,
        padding_s REAL NOT NULL,
        min_keep_s REAL NOT NULL
    );
    CREATE TABLE silence_ranges (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        start_s REAL NOT NULL,
        end_s REAL NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1
    );
    CREATE INDEX silence_ranges_project ON silence_ranges(project_id, start_s);
    CREATE TABLE subtitle_styles (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        builtin INTEGER NOT NULL DEFAULT 0,
        font TEXT NOT NULL,
        size REAL NOT NULL,
        primary_color TEXT NOT NULL,
        highlight_color TEXT NOT NULL,
        outline_color TEXT NOT NULL,
        outline REAL NOT NULL,
        shadow REAL NOT NULL,
        position_y REAL,
        mode TEXT NOT NULL,
        max_chars INTEGER NOT NULL,
        max_lines INTEGER NOT NULL,
        uppercase INTEGER NOT NULL
    );
    CREATE TABLE clips (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        start_s REAL NOT NULL,
        end_s REAL NOT NULL,
        aspect TEXT NOT NULL,
        crop_mode TEXT NOT NULL,
        crop_x REAL NOT NULL,
        style_id INTEGER REFERENCES subtitle_styles(id) ON DELETE SET NULL,
        normalize INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    );
    CREATE TABLE transcripts (
        clip_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
        model TEXT NOT NULL,
        language TEXT NOT NULL,
        words TEXT NOT NULL,
        cache_key TEXT NOT NULL,
        edited INTEGER NOT NULL DEFAULT 0,
        stale INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE export_jobs (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        target_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        progress REAL NOT NULL DEFAULT 0,
        output_path TEXT,
        error TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        finished_at TEXT
    );
    CREATE TABLE app_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        output_dir TEXT NOT NULL,
        encoder TEXT,
        whisper_model TEXT,
        device TEXT,
        mic_volume REAL NOT NULL DEFAULT 1.0,
        game_volume REAL NOT NULL DEFAULT 0.6,
        glossary TEXT NOT NULL DEFAULT '{}'
    );
    """,
)


def default_db_path() -> Path:
    """Return ``<data dir>/clipapp.db``."""
    return data_dir() / DB_NAME


class Database:
    """One SQLite connection shared by the API threads and the job worker.

    Every call holds a re-entrant lock, so statements from different threads never interleave
    inside a ``transaction()``. Rows are returned as plain dicts.

    Args:
        path: Database file (created and migrated if needed).

    Raises:
        sqlite3.Error: the file cannot be opened or migrated.
    """

    def __init__(self, path: Path) -> None:
        """Open (creating if needed) and migrate the database."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        """Close the connection."""
        with self.lock:
            self.conn.close()

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        """Run one statement and return its cursor (``lastrowid``, ``rowcount``)."""
        with self.lock:
            return self.conn.execute(sql, params)

    def one(self, sql: str, params: Any = ()) -> dict[str, Any] | None:
        """Return the first row of a query as a dict, or None."""
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        """Return every row of a query as dicts."""
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run the enclosed statements atomically (rolled back on any exception)."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    @property
    def version(self) -> int:
        """Return the current schema version."""
        row = self.one("SELECT version FROM schema_version")
        return int(row["version"]) if row else 0

    def _migrate(self) -> None:
        self.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        with self.transaction():
            if self.one("SELECT version FROM schema_version") is None:
                self.execute("INSERT INTO schema_version VALUES (0)")
        for number, script in enumerate(MIGRATIONS[self.version :], self.version + 1):
            with self.transaction():
                for statement in script.split(";"):
                    if statement.strip():
                        self.execute(statement)
                self.execute("UPDATE schema_version SET version = ?", (number,))
                if number == 1:
                    self._seed()

    def _seed(self) -> None:
        """Insert the factory styles and the settings row (part of migration 1)."""
        for style in TEMPLATES.values():
            insert_style(self, style, builtin=True)
        out = Path.home() / "Videos" / "ClipApp"
        self.execute("INSERT INTO app_settings (id, output_dir) VALUES (1, ?)", (str(out),))


def insert_style(db: Database, style: SubtitleStyle, builtin: bool = False) -> int:
    """Store a subtitle style and return its id."""
    cols = ", ".join(STYLE_FIELDS)
    marks = ", ".join("?" for _ in STYLE_FIELDS)
    values = [getattr(style, f) for f in STYLE_FIELDS]
    cur = db.execute(
        f"INSERT INTO subtitle_styles (builtin, {cols}) VALUES (?, {marks})",
        (int(builtin), *values),
    )
    return int(cur.lastrowid or 0)
