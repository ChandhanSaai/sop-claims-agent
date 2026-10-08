from typing import Any, Protocol

from app.engine.state import Session


class ChatResultLike(Protocol):
    reply: str
    trace: dict[str, Any]


class ChatService(Protocol):
    def start(self, scenario: str) -> Session: ...

    def chat(self, session: Session, message: str) -> ChatResultLike: ...

    def outbox(self, session: Session) -> list[dict[str, Any]]: ...
