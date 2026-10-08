import time

from app.engine.state import Session


class SessionStore:
    """In-memory, per-process. Redis is the production upgrade (documented limitation)."""

    def __init__(self, ttl_minutes: int):
        self._ttl = ttl_minutes * 60
        self._items: dict[str, tuple[Session, float]] = {}

    def put(self, session: Session) -> None:
        self._items[session.id] = (session, time.monotonic())

    def get(self, session_id: str) -> Session:
        session, last = self._items[session_id]
        if time.monotonic() - last > self._ttl:
            del self._items[session_id]
            raise KeyError(session_id)
        self._items[session_id] = (session, time.monotonic())
        return session

    def sweep(self) -> None:
        now = time.monotonic()
        for sid in [k for k, (_, last) in self._items.items() if now - last > self._ttl]:
            del self._items[sid]
