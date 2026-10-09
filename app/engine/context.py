import re
from typing import Literal

from pydantic import BaseModel, Field

from app.data.normalize import parse_ordinal
from app.engine.state import PendingAsk, Session
from app.llm.schemas import ReplyBrief, TurnAnalysis

HUMAN_ASK = "Would you like me to connect you with a representative?"


class TurnContext(BaseModel):
    analysis: TurnAnalysis
    user_text: str
    pending_at_start: PendingAsk
    changed_slots: list[str] = Field(default_factory=list)
    confirm_yes: bool = False
    confirm_no: bool = False
    email_yes: bool = False
    email_no: bool = False
    email_confirm_yes: bool = False
    email_confirm_no: bool = False
    anything_else_no: bool = False
    human_yes: bool = False
    human_no: bool = False
    selection_case_id: str | None = None
    selection_ordinal: int | None = None
    extra_must_say: list[str] = Field(default_factory=list)
    policy_brief: ReplyBrief | None = None
    tone: Literal["neutral", "warm", "de_escalate"] = "neutral"
    acknowledge: str | None = None
    offer_human: bool = False


def resolve_pending(session: Session, analysis: TurnAnalysis, user_text: str) -> TurnContext:
    """Code, not the model, maps short answers onto the question that was pending."""
    r = analysis.requests
    yes, no = r.confirmation == "yes", r.confirmation == "no"
    ctx = TurnContext(analysis=analysis, user_text=user_text, pending_at_start=session.pending_ask,
                      confirm_yes=yes, confirm_no=no)
    p = session.pending_ask
    if p == PendingAsk.EMAIL_OFFER:
        ctx.email_yes = yes or r.email_summary == "yes"
        ctx.email_no = no or r.email_summary == "no"
    elif p == PendingAsk.EMAIL_CONFIRM:
        ctx.email_confirm_yes = yes or r.email_summary == "yes"
        ctx.email_confirm_no = no or r.email_summary == "no"
    elif p == PendingAsk.ANYTHING_ELSE:
        ctx.anything_else_no = (no or r.closing) and analysis.intent == "none" and not analysis.question
    elif p == PendingAsk.HUMAN_OFFER:
        ctx.human_yes = yes or r.wants_human
        ctx.human_no = no
    elif p == PendingAsk.DISAMBIGUATION:
        ctx.selection_case_id = analysis.case_hints.case_id
        ctx.selection_ordinal = parse_ordinal(user_text)
    elif p == PendingAsk.IDENTITY_FIELDS and analysis.identity.id_last4 is None:
        if re.fullmatch(r"\s*\d{4}\s*", user_text):
            analysis.identity.id_last4 = user_text.strip()
    return ctx
