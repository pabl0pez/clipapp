"""Run the API with uvicorn on 127.0.0.1 and announce the port on stdout."""

import json
import socket
import sys
from pathlib import Path

import uvicorn

from clipapp_engine.api.app import create_app

HOST = "127.0.0.1"


def bind(port: int) -> socket.socket:
    """Bind a listening socket on 127.0.0.1 (``port=0`` lets the OS pick a free one)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, port))
    sock.listen(128)
    sock.set_inheritable(True)
    return sock


class _Server(uvicorn.Server):
    """uvicorn server that prints ``{"ready": true, "port": N}`` once it accepts requests."""

    port = 0

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        """Start as usual, then print the ready line (the only stdout output)."""
        await super().startup(sockets)
        if not self.should_exit:
            sys.stdout.write(json.dumps({"ready": True, "port": self.port}) + "\n")
            sys.stdout.flush()


def serve(port: int, token: str, db_path: Path | None = None) -> None:
    """Serve the engine API until interrupted.

    Args:
        port: TCP port on 127.0.0.1; 0 picks a free one.
        token: Per-session secret required on every request.
        db_path: SQLite file; defaults to ``<data dir>/clipapp.db``.

    Raises:
        OSError: the port cannot be bound.
    """
    sock = bind(port)
    # uvicorn logs go to stderr; the access log (stdout) is off so stdout stays machine-readable.
    config = uvicorn.Config(create_app(token, db_path), access_log=False, log_level="info")
    server = _Server(config)
    server.port = sock.getsockname()[1]
    server.run(sockets=[sock])
