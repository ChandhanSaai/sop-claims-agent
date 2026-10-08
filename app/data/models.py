from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class Policyholder(BaseModel):
    model_config = ConfigDict(extra="ignore")
    party_id: str
    name: str
    name_aliases: list[str] = Field(default_factory=list)
    policy_number: str
    dob: date
    id_type: str
    id_last4: str
    phone: str
    phone_aliases: list[str] = Field(default_factory=list)
    email: str
    email_aliases: list[str] = Field(default_factory=list)


class Claim(BaseModel):
    model_config = ConfigDict(extra="ignore")
    case_id: str
    party_id: str
    case_type: str
    created_at: date
    status: str
    summary: str
    denial_reason: str | None = None
    documents_needed: list[str] = Field(default_factory=list)
    appeal_deadline: date | None = None
    expected_reimbursement_amount: str
    allowed_max_amount: str
    net_pay: str
    net_fee: str


class Representative(BaseModel):
    model_config = ConfigDict(extra="ignore")
    rep_name: str
    relationship: str
    buyer_name: str
    buyer_party_id: str


class FollowupTopic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    topic: str
    intent_hints: list[str] = Field(default_factory=list)
    requires_documents: bool = False
    match_any: list[str] = Field(default_factory=list)
    en: str


class Guideline(BaseModel):
    model_config = ConfigDict(extra="ignore")
    default_guidance: dict[str, str]
    case_type_guidance: dict[str, dict[str, str]]
    document_guidance: dict[str, dict[str, str]]
    document_alternative_guidance: dict[str, dict[str, str]]
    claim_followup_settings: dict[str, dict[str, str]]
    claim_followup_guidance: list[FollowupTopic]
    claim_followup_fallback: dict[str, str]


class ConsentScenario(BaseModel):
    status_sequence: list[str]


class EmailRecord(BaseModel):
    id: str
    to: str
    to_masked: str
    subject: str
    body: str
    sent_at: str
