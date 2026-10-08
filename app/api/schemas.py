from typing import Any

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    # consent scenario name from fixtures/consent_scenarios.json; None: the configured CONSENT_SCENARIO
    scenario: str | None = None


class SessionCreateResponse(BaseModel):
    session_id: str
    greeting: str
    state: dict[str, Any]


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    reply: str
    state: dict[str, Any]
    trace: dict[str, Any]


class OutboxResponse(BaseModel):
    emails: list[dict[str, Any]]


class TraceResponse(BaseModel):
    turns: list[dict[str, Any]]
