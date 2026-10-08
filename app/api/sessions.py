import threading
import time

from app.engine.state import Session


class SessionStore:
    """In-memory, per-process. Redis is the production upgrade (documented limitation)."""

    def __init__(self, ttl_minutes: int):
        self._ttl = ttl_minutes * 60
        self._items: dict[str, tuple[Session, float, threading.Lock]] = {}

    def put(self, session: Session) -> None:
        self.sweep()
        self._items[session.id] = (session, time.monotonic(), threading.Lock())

    def get(self, session_id: str) -> Session:
        session, last, lock = self._items[session_id]
        if time.monotonic() - last > self._ttl:
            del self._items[session_id]
            raise KeyError(session_id)
        self._items[session_id] = (session, time.monotonic(), lock)
        return session

    def lock(self, session_id: str) -> threading.Lock:
        """Hold while reading or mutating the session: sync routes run on worker threads."""
        return self._items[session_id][2]

    def sweep(self) -> None:
        now = time.monotonic()
        for sid, (_, last, _) in list(self._items.items()):
            if now - last > self._ttl:
                self._items.pop(sid, None)  # a concurrent get() may have evicted it already
