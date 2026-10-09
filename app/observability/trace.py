import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.engine.state import Session
from app.llm.schemas import ReplyBrief, TurnAnalysis
from app.observability.logging import redact

MASKED_IDENTITY = ("dob", "phone", "email", "id_last4", "policy_number")
REDACTED_FIELDS = ("analysis", "brief", "guard", "changed_slots")  # free text; ts and ids stay exact


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
        for c in a["corrections"]:
            c["new_value"] = "******"
        return cls(
            ts=datetime.now(UTC).isoformat(), session_id=session_id, turn=turn, phase_before=phase_before,
            phase_after=phase_after, pending_ask=pending_ask, analysis=a, changed_slots=changed_slots,
            brief=brief.model_dump(exclude={"verbatim"}), guard=guard, reader_model=reader_model,
            writer_model=writer_model, latency_ms=latency_ms, reply_chars=len(reply_text))


def _redact_strings(x: Any) -> Any:
    if isinstance(x, str):
        return redact(x)
    if isinstance(x, dict):
        return {k: _redact_strings(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_redact_strings(v) for v in x]
    return x


class TraceWriter:
    def __init__(self, directory: Path):
        self.directory = directory

    @staticmethod
    def redact(record: TraceRecord) -> dict[str, Any]:
        data = record.model_dump()
        data.update({k: _redact_strings(data[k]) for k in REDACTED_FIELDS})
        return data

    def write(self, record: TraceRecord) -> dict[str, Any]:
        """Append the redacted record to the session's JSONL file and return it."""
        data = self.redact(record)
        self.directory.mkdir(parents=True, exist_ok=True)
        with open(self.directory / f"{record.session_id}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")
        return data


def disclosure_event(session: Session, brief: ReplyBrief) -> None:
    """Audit: what claim facts were disclosed, to which role, under which consent."""
    if session.verification.status != "verified" or not brief.allowed_facts:
        return
    session.log("disclosed", role=session.verification.role, facts=sorted(brief.allowed_facts.keys()),
                consent_id=session.consent.consent_id, case_id=session.case.selected_case_id)
