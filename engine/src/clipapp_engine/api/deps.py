"""FastAPI dependencies: shared state access and the per-session token checks."""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, APIKeyQuery
from starlette.requests import HTTPConnection

from clipapp_engine.api.events import EventHub
from clipapp_engine.db.database import Database
from clipapp_engine.jobs.queue import JobQueue

TOKEN_HEADER = "X-ClipApp-Token"
TOKEN_QUERY = "token"

_header = APIKeyHeader(name=TOKEN_HEADER, auto_error=False)
_query = APIKeyQuery(name=TOKEN_QUERY, auto_error=False)


def token_ok(conn: HTTPConnection, given: str | None) -> bool:
    """Return True when ``given`` equals the session token (constant-time compare)."""
    expected: str = conn.app.state.token
    return given is not None and secrets.compare_digest(given.encode(), expected.encode())


def require_token(request: Request, header: Annotated[str | None, Security(_header)]) -> None:
    """Reject the request with 401 unless ``X-ClipApp-Token`` holds the session token."""
    if not token_ok(request, header):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing token")


def require_media_token(
    request: Request,
    header: Annotated[str | None, Security(_header)],
    query: Annotated[str | None, Security(_query)],
) -> None:
    """Like ``require_token`` but also accepts ``?token=`` (a ``<video src>`` sends no headers)."""
    if not (token_ok(request, header) or token_ok(request, query)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing token")


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _jobs(request: Request) -> JobQueue:
    jobs: JobQueue = request.app.state.jobs
    return jobs


def _hub(request: Request) -> EventHub:
    hub: EventHub = request.app.state.hub
    return hub


DB = Annotated[Database, Depends(_db)]
Jobs = Annotated[JobQueue, Depends(_jobs)]
Hub = Annotated[EventHub, Depends(_hub)]
