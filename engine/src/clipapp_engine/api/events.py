"""Fan-out of engine events (from any thread) to the ``/events`` WebSocket clients."""

import asyncio
import threading
from typing import Any


class EventHub:
    """Thread-safe publisher: each subscriber is an asyncio queue bound to its event loop."""

    def __init__(self) -> None:
        """Start with no subscribers."""
        self._subs: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Register a queue on the running loop; call ``unsubscribe`` when done."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        with self._lock:
            self._subs.add((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a queue returned by ``subscribe``."""
        with self._lock:
            self._subs = {s for s in self._subs if s[1] is not q}

    def publish(self, event: dict[str, Any]) -> None:
        """Deliver an event to every subscriber; safe to call from worker threads."""
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:  # loop already closed: the client is gone
                self.unsubscribe(q)
