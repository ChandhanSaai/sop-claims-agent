import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.config import Settings
from app.data.repos import Repos, build_repos
from app.data.store import FixtureStore
from app.engine.briefs import render_brief
from app.engine.guard import GuardResult, OutputGuard
from app.engine.machine import Engine
from app.engine.state import Session, Turn
from app.llm.anthropic_client import LLMError, build_llm
from app.llm.base import LLM
from app.llm.fake import FakeLLM
from app.llm.schemas import ReplyBrief
from app.observability.trace import TraceRecord, TraceWriter, disclosure_event

log = logging.getLogger(__name__)
TROUBLE = "I'm having trouble responding right now. Could you say that again in a moment?"
CLOSED_TEXT = ("This conversation has ended. A representative will follow up; the reference is {reference}.")


@dataclass
class ChatResult:
    reply: str
    trace: dict[str, Any] = field(default_factory=dict)


class ConversationService:
    def __init__(self, engine: Engine, llm: LLM, guard: OutputGuard, repos: Repos, settings: Settings,
                 trace_writer: TraceWriter):
        self.engine, self.llm, self.guard, self.repos, self.settings, self.trace_writer = (
            engine, llm, guard, repos, settings, trace_writer)

    def start(self, scenario: str | None = None) -> Session:
        if scenario is None:  # CONSENT_SCENARIO is the default; an explicit scenario wins
            scenario = self.settings.consent_scenario
        if scenario not in self.repos.store.consent_scenarios:
            scenario = "default"
        session = Session.new(scenario)
        self.engine.greeting(session)
        return session

    def chat(self, session: Session, message: str) -> ChatResult:
        if session.closed:  # ended by policy: no model call, no state change
            session.log("message_after_close")
            return ChatResult(reply=CLOSED_TEXT.format(reference=session.escalation.reference))
        t0 = time.perf_counter()
        phase_before = session.phase.value
        slots_before = {k: v.value for k, v in session.memory.slots.items()}
        try:
            analysis = self.llm.analyze(user_text=message, pending_ask=session.pending_ask.value,
                                        last_assistant=session.last_assistant_text())
        except LLMError as e:  # the turn is dropped whole: no turn count, no transcript, no trace
            log.warning("reader failed: %s", e)
            session.log("llm_error", stage="reader")
            return ChatResult(reply=TROUBLE)
        brief = self.engine.handle_turn(session, analysis, message)
        text, guard = self._compose_guarded(session, brief)
        if brief.verbatim:
            text = f"{text}\n\n{brief.verbatim}"
        session.transcript.append(Turn(role="assistant", text=text))
        disclosure_event(session, brief)
        session.last_guard = guard
        changed = [k for k, v in session.memory.slots.items() if slots_before.get(k) != v.value]
        record = TraceRecord.build(
            session_id=session.id, turn=session.turn, phase_before=phase_before,
            phase_after=session.phase.value, pending_ask=session.pending_ask.value, analysis=analysis,
            changed_slots=changed, brief=brief, guard=guard,
            reader_model=self.settings.reader_model, writer_model=self.settings.writer_model,
            latency_ms=int((time.perf_counter() - t0) * 1000), reply_text=text,
        )
        try:
            trace = self.trace_writer.write(record)
        except OSError:  # observability never fails the caller's turn
            log.exception("trace write failed")
            trace = self.trace_writer.redact(record)
        session.traces.append(trace)
        return ChatResult(reply=text, trace=trace)

    def _compose_guarded(self, session: Session, brief: ReplyBrief) -> tuple[str, dict[str, Any]]:
        try:
            text = self.llm.compose(brief=brief, transcript=session.transcript[session.transcript_fence:])
        except LLMError as e:  # the state already moved (an email may have gone out): the reply must agree
            log.warning("writer failed: %s", e)
            session.log("llm_error", stage="writer")
            return self._fallback(session, brief, "llm_error")
        result = self.guard.check(text, session, brief)
        if result.ok:
            return text, result.model_dump()
        session.log("guard_violation", violations=result.violations, attempt=1)
        try:
            text = self.llm.compose(brief=brief, transcript=session.transcript[session.transcript_fence:],
                                    violation="; ".join(result.violations))
        except LLMError as e:
            log.warning("writer regenerate failed: %s", e)
            session.log("llm_error", stage="writer_regenerate")
            return self._fallback(session, brief, "llm_error", result)
        result2 = self.guard.check(text, session, brief)
        if result2.ok:
            return text, {**result2.model_dump(), "regenerated": True}
        session.log("guard_violation", violations=result2.violations, attempt=2)
        return self._fallback(session, brief, "template", result2)

    def _fallback(self, session: Session, brief: ReplyBrief, why: str,
                  draft: GuardResult | None = None) -> tuple[str, dict[str, Any]]:
        """The templated rendering is guarded like any reply: the trace records the verdict on the text sent,
        why a template was sent, and what the guard rejected in the Writer's last draft."""
        text = render_brief(brief)
        verdict = {**self.guard.check(text, session, brief).model_dump(), "fallback": why}
        if draft is not None:
            verdict["draft_violations"] = draft.violations
        return text, verdict

    def outbox(self, session: Session) -> list[dict[str, Any]]:
        ids = {e.data.get("email_id") for e in session.fenced_events() if e.type == "email_sent"}
        return [r.model_dump(exclude={"to"}) for r in self.repos.outbox.list() if r.id in ids]


def build_service(settings: Settings, llm: LLM | None = None,
                  today: Callable[[], date] = date.today) -> ConversationService:
    store = FixtureStore.load(settings.fixtures_dir)
    repos = build_repos(store, settings)
    if llm is None:
        llm = FakeLLM() if settings.llm_backend == "fake" else build_llm(settings)
    return ConversationService(Engine(repos, settings, today), llm, OutputGuard(store), repos, settings,
                               TraceWriter(settings.traces_dir))
