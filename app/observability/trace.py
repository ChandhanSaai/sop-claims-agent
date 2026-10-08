import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.engine.state import Session
from app.llm.schemas import ReplyBrief, TurnAnalysis
from app.observability.logging import redact

MASKED_IDENTITY = ("dob", "phone", "email", "id_last4", "policy_number")


class TraceRecord(BaseModel):
    ts: str
    session_id: str
    turn: int
    phase_before: str
    phase_after: str
    pending_ask: str
    analysis: dict[str, Any]
    changed_slots: list[str]
    brief: dict[str, Any]
    guard: dict[str, Any]
    reader_model: str
    writer_model: str
    latency_ms: int
    reply_chars: int
    prompt_version: str = "v1"
    usage: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def build(cls, *, session_id: str, turn: int, phase_before: str, phase_after: str, pending_ask: str,
              analysis: TurnAnalysis, changed_slots: list[str], brief: ReplyBrief, guard: dict[str, Any],
              reader_model: str, writer_model: str, latency_ms: int, reply_text: str) -> "TraceRecord":
        a = analysis.model_dump()
        for k in MASKED_IDENTITY:
            if a["identity"].get(k):
                a["identity"][k] = "******"
        return cls(
            ts=datetime.now(UTC).isoformat(), session_id=session_id, turn=turn, phase_before=phase_before,
            phase_after=phase_after, pending_ask=pending_ask, analysis=a, changed_slots=changed_slots,
            brief=brief.model_dump(exclude={"verbatim"}), guard=guard, reader_model=reader_model,
            writer_model=writer_model, latency_ms=latency_ms, reply_chars=len(reply_text))


class TraceWriter:
    def __init__(self, directory: Path):
        self.directory = directory

    def write(self, record: TraceRecord) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with open(self.directory / f"{record.session_id}.jsonl", "a", encoding="utf-8") as f:
            f.write(redact(json.dumps(record.model_dump())) + "\n")


def disclosure_event(session: Session, brief: ReplyBrief) -> None:
    """Audit: what claim facts were disclosed, to which role, under which consent."""
    if session.verification.status != "verified" or not brief.allowed_facts:
        return
    session.log("disclosed", role=session.verification.role, facts=sorted(brief.allowed_facts.keys()),
                consent_id=session.consent.consent_id, case_id=session.case.selected_case_id)
