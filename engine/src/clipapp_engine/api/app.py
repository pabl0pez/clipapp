"""FastAPI application factory: state, token security, CORS, errors and the ``/events`` socket."""

import asyncio
import wave
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from clipapp_engine.api import clips, core, projects
from clipapp_engine.api.deps import TOKEN_HEADER, TOKEN_QUERY, token_ok
from clipapp_engine.api.events import EventHub
from clipapp_engine.api.service import ConflictError, NotFoundError, make_handlers
from clipapp_engine.db.database import Database, default_db_path
from clipapp_engine.jobs.queue import JobQueue
from clipapp_engine.media.encoders import EncoderError
from clipapp_engine.media.errors import MediaError, MediaNotFoundError

# Tauri 2 webview origins on Windows (http/https) and macOS/Linux, plus the Vite dev server.
ALLOWED_ORIGINS = [
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
]
ERRORS: list[tuple[type[Exception], int]] = [
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (MediaNotFoundError, status.HTTP_404_NOT_FOUND),
    (ConflictError, status.HTTP_409_CONFLICT),
    (EncoderError, status.HTTP_409_CONFLICT),
    (MediaError, status.HTTP_422_UNPROCESSABLE_CONTENT),
    (ValidationError, status.HTTP_422_UNPROCESSABLE_CONTENT),
    (ValueError, status.HTTP_422_UNPROCESSABLE_CONTENT),
    (wave.Error, status.HTTP_422_UNPROCESSABLE_CONTENT),
]


def create_app(token: str, db_path: Path | None = None, start_worker: bool = True) -> FastAPI:
    """Build the engine API.

    Args:
        token: Per-session secret every request must carry.
        db_path: SQLite file; defaults to ``<data dir>/clipapp.db``.
        start_worker: Start the job worker with the app (tests may drive jobs by hand).

    Returns:
        The FastAPI app; the database opens and interrupted jobs are recovered right away,
        the worker runs during the app's lifespan.
    """
    db = Database(db_path or default_db_path())
    hub = EventHub()
    jobs = JobQueue(db, make_handlers(db, hub.publish), hub.publish)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if start_worker:
            jobs.start()
        try:
            yield
        finally:
            if start_worker:
                jobs.stop()
            db.close()

    app = FastAPI(title="ClipApp engine", version="1", lifespan=lifespan)
    app.state.token, app.state.db, app.state.hub, app.state.jobs = token, db, hub, jobs
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=[TOKEN_HEADER, "Content-Type", "Range"],
        expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
    )
    for exc_type, code in ERRORS:

        def handler(_req: Request, exc: Exception, code: int = code) -> JSONResponse:
            return JSONResponse({"detail": str(exc).strip()}, status_code=code)

        app.add_exception_handler(exc_type, handler)

    for module in (core, projects, clips):
        app.include_router(module.router)
    app.include_router(projects.media_router)

    @app.websocket("/events")
    async def events(ws: WebSocket) -> None:
        """Push ``job_progress``, ``job_status`` and ``project_updated`` events as JSON."""
        given = ws.headers.get(TOKEN_HEADER) or ws.query_params.get(TOKEN_QUERY)
        if not token_ok(ws, given):
            denial = JSONResponse({"detail": "invalid or missing token"}, status_code=401)
            await ws.send_denial_response(denial)
            return
        await ws.accept()
        q = hub.subscribe()

        async def pump() -> None:
            while True:
                await ws.send_json(await q.get())

        sender = asyncio.create_task(pump())
        try:
            while True:  # reading is how a client disconnect is noticed
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            hub.unsubscribe(q)

    return app
