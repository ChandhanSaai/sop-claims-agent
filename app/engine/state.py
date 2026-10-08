import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.llm.schemas import ReplyBrief


class Phase(StrEnum):
    VERIFY_ID = "VERIFY_ID"
    RESOLVE_INTENT = "RESOLVE_INTENT"
    PROCESS_CASE = "PROCESS_CASE"
    POST_PROCESS = "POST_PROCESS"


class PendingAsk(StrEnum):
    NONE = "none"
    IDENTITY_FIELDS = "identity_fields"
    DOB_FORMAT = "dob_format"
    DISAMBIGUATION = "disambiguation"
    ANYTHING_ELSE = "anything_else"
    EMAIL_OFFER = "email_offer"
    EMAIL_CONFIRM = "email_confirm"
    HUMAN_OFFER = "human_offer"
    CONSENT_WAIT = "consent_wait"


class SlotStatus(StrEnum):
    PROVISIONAL = "provisional"
    VERIFIED = "verified"
    REJECTED = "rejected"


IDENTITY_SLOTS = ("full_name", "dob", "phone", "email", "id_last4", "policy_number")
HINT_SLOTS = ("case_type", "status_hint", "month", "year", "case_id", "free_text", "intent")
# Slot values that are safe to show in the inspector; everything else is masked.
UNMASKED_SLOTS = ("full_name", *HINT_SLOTS)


class Slot(BaseModel):
    value: str
    source_turn: int
    status: SlotStatus = SlotStatus.PROVISIONAL


class Memory(BaseModel):
    slots: dict[str, Slot] = Field(default_factory=dict)

    def get(self, name: str) -> Slot | None:
        return self.slots.get(name)

    def value(self, name: str) -> str | None:
        s = self.slots.get(name)
        return s.value if s and s.status != SlotStatus.REJECTED else None

    def set(self, name: str, value: str, turn: int, *, overwrite_verified: bool = False) -> bool:
        """Store a provisional value. Verified slots change only through a correction.
        Returns True if changed."""
        cur = self.slots.get(name)
        if cur and cur.status == SlotStatus.VERIFIED and not overwrite_verified:
            return False
        if cur and cur.value == value and cur.status != SlotStatus.REJECTED:
            return False
        self.slots[name] = Slot(value=value, source_turn=turn)
        return True

    def mark_verified(self, names) -> None:
        for n in names:
            if n in self.slots:
                self.slots[n].status = SlotStatus.VERIFIED

    def reset_identity(self) -> None:
        for n in IDENTITY_SLOTS:
            if n in self.slots:
                self.slots[n].status = SlotStatus.PROVISIONAL


class Verification(BaseModel):
    status: Literal["unverified", "verified", "exhausted"] = "unverified"
    party_id: str | None = None
    attempts: int = 0
    role: Literal["policyholder", "representative"] | None = None
    # identifiers used in the last verify call; a repeat is not a new attempt
    last_fingerprint: str | None = None


class Consent(BaseModel):
    status: Literal["none", "pending", "approved", "timed_out"] = "none"
    representative_name: str | None = None
    polls: int = 0
    consent_id: str | None = None


class CaseState(BaseModel):
    candidates: list[str] = Field(default_factory=list)
    selected_case_id: str | None = None
    intent: str | None = None
    answered_once: bool = False


class Escalation(BaseModel):
    requested: bool = False
    reference: str | None = None
    reason: str | None = None


class Counters(BaseModel):
    off_topic: int = 0
    frustration_streak: int = 0
    gate_explanations: int = 0
    email_offered: bool = False


class Event(BaseModel):
    turn: int
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class Session(BaseModel):
    id: str
    created_at: str
    scenario: str = "default"
    phase: Phase = Phase.VERIFY_ID
    turn: int = 0
    memory: Memory = Field(default_factory=Memory)
    verification: Verification = Field(default_factory=Verification)
    consent: Consent = Field(default_factory=Consent)
    case: CaseState = Field(default_factory=CaseState)
    pending_ask: PendingAsk = PendingAsk.NONE
    escalation: Escalation = Field(default_factory=Escalation)
    counters: Counters = Field(default_factory=Counters)
    events: list[Event] = Field(default_factory=list)
    transcript: list[Turn] = Field(default_factory=list)
    pending_draft: str | None = None
    last_brief: ReplyBrief | None = None
    last_guard: dict[str, Any] | None = None
    traces: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def new(cls, scenario: str = "default") -> "Session":
        return cls(id=uuid.uuid4().hex, created_at=datetime.now(UTC).isoformat(), scenario=scenario)

    def log(self, type: str, **data: Any) -> None:
        self.events.append(Event(turn=self.turn, type=type, data=data))

    def last_assistant_text(self) -> str | None:
        for t in reversed(self.transcript):
            if t.role == "assistant":
                return t.text
        return None

    def snapshot(self) -> dict[str, Any]:
        """Inspector view. Identifier values are masked; nothing here is sent to the model."""
        memory = {
            name: {
                "value": s.value if name in UNMASKED_SLOTS else "******",
                "status": s.status.value,
                "source_turn": s.source_turn,
            }
            for name, s in self.memory.slots.items()
        }
        return {
            "session_id": self.id,
            "scenario": self.scenario,
            "phase": self.phase.value,
            "turn": self.turn,
            "pending_ask": self.pending_ask.value,
            "verification": self.verification.model_dump(exclude={"last_fingerprint"}),
            "consent": self.consent.model_dump(),
            "case": self.case.model_dump(),
            "escalation": self.escalation.model_dump(),
            "counters": self.counters.model_dump(),
            "memory": memory,
            "last_brief": self.last_brief.model_dump() if self.last_brief else None,
            "last_guard": self.last_guard,
            "events": [e.model_dump() for e in self.events[-30:]],
        }
