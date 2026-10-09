from typing import Protocol

from app.engine.state import Turn
from app.llm.schemas import ReplyBrief, TurnAnalysis


class LLM(Protocol):
    def analyze(self, *, user_text: str, pending_ask: str, last_assistant: str | None) -> TurnAnalysis: ...

    def compose(self, *, brief: ReplyBrief, transcript: list[Turn], violation: str | None = None) -> str: ...
