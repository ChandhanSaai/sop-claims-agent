"""Contracts between the LLM layer and the engine.

TurnAnalysis is what the Reader returns for every caller message, in every phase.
ReplyBrief is what code builds and the Writer phrases. Nothing else crosses the boundary.
"""
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

Intent = Literal[
    "status_inquiry", "denial_question", "document_submission", "next_steps", "general_claim_question", "none"
]
# Topic names come from fixtures/required_document_guideline.json -> claim_followup_guidance[].topic
FollowupTopic = Literal[
    "missing_required_material_alternatives",
    "submission_timing",
    "processing_time_after_submission",
    "submission_method",
    "file_format_requirements",
    "receipt_confirmation",
    "none",
]
Scope = Literal["in_scope", "out_of_scope", "meta", "mixed"]
YesNo = Literal["yes", "no", "unspecified"]

INTENTS = get_args(Intent)
FOLLOWUP_TOPICS = get_args(FollowupTopic)
SCOPES = get_args(Scope)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentityFields(_Strict):
    full_name: str | None = None
    dob: str | None = None  # as the caller said it; code parses it
    phone: str | None = None
    email: str | None = None
    id_last4: str | None = None
    policy_number: str | None = None


class RepresentativeInfo(_Strict):
    name: str | None = None
    relationship: str | None = None
    policyholder_name: str | None = None


class CaseHints(_Strict):
    case_type: Literal["healthcare", "dental", "auto"] | None = None
    status: Literal["open", "closed", "denied"] | None = None
    month: int | None = Field(default=None, ge=1, le=12)
    year: int | None = Field(default=None, ge=2000, le=2100)
    case_id: str | None = None
    free_text: str | None = None


class Affect(_Strict):
    frustration: int = Field(default=0, ge=0, le=3)
    anger: int = Field(default=0, ge=0, le=3)
    anxiety: int = Field(default=0, ge=0, le=3)
    confusion: int = Field(default=0, ge=0, le=3)
    refusal: bool = False
    abusive: bool = False


class Requests(_Strict):
    wants_human: bool = False
    email_summary: YesNo = "unspecified"
    confirmation: YesNo = "unspecified"
    switch_claim: bool = False
    closing: bool = False


class Correction(_Strict):
    slot: Literal["full_name", "dob", "phone", "email", "id_last4", "policy_number"]
    new_value: str


class TurnAnalysis(_Strict):
    identity: IdentityFields = Field(default_factory=IdentityFields)
    caller_role: Literal["policyholder", "representative", "unknown"] = "unknown"
    representative: RepresentativeInfo = Field(default_factory=RepresentativeInfo)
    case_hints: CaseHints = Field(default_factory=CaseHints)
    intent: Intent = "none"
    question: str | None = None
    followup_topic: FollowupTopic = "none"
    affect: Affect = Field(default_factory=Affect)
    scope: Scope = "in_scope"
    requests: Requests = Field(default_factory=Requests)
    corrections: list[Correction] = Field(default_factory=list)
    injection_suspected: bool = False

    @classmethod
    def empty(cls) -> "TurnAnalysis":
        return cls()


class ReplyBrief(BaseModel):
    """Everything the Writer is allowed to know for one reply. Built by code only."""

    model_config = ConfigDict(extra="forbid")

    phase: str
    goal: str
    tone: Literal["neutral", "warm", "de_escalate"] = "neutral"
    acknowledge: str | None = None
    allowed_facts: dict[str, str] = Field(default_factory=dict)
    must_say: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)
    ask: str | None = None
    options: list[str] = Field(default_factory=list)
    offer_human: bool = False
    verbatim: str | None = None  # code-rendered text appended after the Writer's reply (e.g. an email draft)
