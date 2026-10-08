from datetime import date

from app.config import Settings
from app.data.models import Claim
from app.data.normalize import fmt_date
from app.data.repos import Repos
from app.engine.briefs import HandlerResult
from app.engine.context import TurnContext
from app.engine.state import PendingAsk, Phase, Session
from app.llm.schemas import ReplyBrief

HINT_SLOT_NAMES = ("case_type", "status_hint", "month", "year", "case_id")


def hints_from_memory(session: Session) -> dict:
    m = session.memory
    return {
        "case_type": m.value("case_type"),
        "status": m.value("status_hint"),
        "month": int(m.value("month")) if m.value("month") else None,
        "year": int(m.value("year")) if m.value("year") else None,
        "case_id": m.value("case_id"),
    }


def drop_stale_hints(session: Session, changed: list[str]) -> None:
    """When the caller moves to another claim, the old claim's hints and intent must not carry over."""
    for n in (*HINT_SLOT_NAMES, "intent"):
        if n not in changed:
            session.memory.slots.pop(n, None)


def describe(c: Claim) -> str:
    return f"{c.case_id}, {c.case_type} claim opened {fmt_date(c.created_at)}, status {c.status}"


def _select(session: Session, claim: Claim) -> HandlerResult:
    session.case.selected_case_id = claim.case_id
    session.case.candidates = [claim.case_id]
    session.case.intent = session.memory.value("intent") or "general_claim_question"
    session.case.answered_once = False
    session.log("claim_selected", case_id=claim.case_id, intent=session.case.intent)
    session.phase = Phase.PROCESS_CASE
    session.pending_ask = PendingAsk.NONE
    fact = f"The claim you mentioned is {describe(claim)}."
    facts = {
        "claim_id": claim.case_id, "claim_type": claim.case_type,
        "claim_status": claim.status, "claim_opened": fmt_date(claim.created_at),
    }
    brief = ReplyBrief(phase=Phase.PROCESS_CASE.value, goal="Claim identified; continue.",
                       must_say=[fact], allowed_facts=facts)
    return HandlerResult(brief=brief, advanced=True, needs_input=False,
                         transition_fact=fact, transition_facts=facts)


def handle(
    session: Session, ctx: TurnContext, repos: Repos, settings: Settings, today: date
) -> HandlerResult:
    claims = repos.claims.for_party(session.verification.party_id)
    by_id = {c.case_id: c for c in claims}
    hint_changed = any(n in ctx.changed_slots for n in HINT_SLOT_NAMES)

    if ctx.pending_at_start == PendingAsk.DISAMBIGUATION:
        pick = None
        if ctx.selection_case_id and ctx.selection_case_id.upper() in session.case.candidates:
            pick = ctx.selection_case_id.upper()
        elif ctx.selection_ordinal and 1 <= ctx.selection_ordinal <= len(session.case.candidates):
            pick = session.case.candidates[ctx.selection_ordinal - 1]
        if pick:
            return _select(session, by_id[pick])

    selected = by_id.get(session.case.selected_case_id)
    # a new hint that still fits the selected claim (its year, say) keeps it instead of re-searching
    if selected and (not hint_changed or repos.claims.filter([selected], **hints_from_memory(session))):
        return _select(session, selected)
    if session.case.selected_case_id and hint_changed:
        drop_stale_hints(session, ctx.changed_slots)

    hints = hints_from_memory(session)
    candidates = repos.claims.filter(claims, **hints)
    any_hint = any(v is not None for v in hints.values())
    if len(candidates) == 1:
        return _select(session, candidates[0])
    no_match = any_hint and not candidates
    if not candidates:
        candidates = claims
    session.case.candidates = [c.case_id for c in candidates]
    session.case.selected_case_id = None
    facts = {f"option_{i + 1}": describe(c) for i, c in enumerate(candidates)}
    must_say = (["I don't see a claim matching that description exactly."] if no_match else []) + [
        "Here are the claims on file."
    ]
    session.pending_ask = PendingAsk.DISAMBIGUATION
    brief = ReplyBrief(phase=Phase.RESOLVE_INTENT.value,
                       goal="Ask which claim the caller means, listing only claims from data.",
                       allowed_facts=facts, must_say=must_say,
                       must_not=["Do not guess which claim they mean."],
                       ask="Which of these would you like to discuss?")
    return HandlerResult(brief=brief)
