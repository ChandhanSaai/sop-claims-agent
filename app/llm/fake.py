from app.engine.briefs import render_brief
from app.engine.state import Turn
from app.llm.schemas import ReplyBrief, TurnAnalysis


class FakeLLM:
    """Replays scripted analyses in order, then empty ones. Renders briefs deterministically."""

    def __init__(self, analyses: list[TurnAnalysis] | None = None):
        self._queue = list(analyses or [])
        self.calls: list[dict] = []

    def queue(self, analysis: TurnAnalysis) -> None:
        self._queue.append(analysis)

    def analyze(self, *, user_text: str, pending_ask: str, last_assistant: str | None) -> TurnAnalysis:
        self.calls.append(
            {"user_text": user_text, "pending_ask": pending_ask, "last_assistant": last_assistant}
        )
        return self._queue.pop(0) if self._queue else TurnAnalysis.empty()

    def compose(self, *, brief: ReplyBrief, transcript: list[Turn], violation: str | None = None) -> str:
        return render_brief(brief)
