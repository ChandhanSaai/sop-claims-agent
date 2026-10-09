from app.engine.state import (
    IDENTITY_SLOTS,
    REP_SLOTS,
    CaseState,
    Escalation,
    PendingAsk,
    Phase,
    Session,
    SlotStatus,
    Verification,
)
from app.llm.schemas import TurnAnalysis


def merge_analysis(session: Session, analysis: TurnAnalysis) -> list[str]:
    """Capture anything early. Only code advances phases; this only records what the caller said."""
    changed: list[str] = []
    t = session.turn
    # corrections first: one that resets verification makes the other identifiers in the same message the
    # new party's values, which the identity loop would otherwise refuse as restatements of verified slots
    _apply_corrections(session, analysis, changed)
    ident = analysis.identity
    for name in IDENTITY_SLOTS:
        val = getattr(ident, name)
        if val and session.memory.set(name, str(val).strip(), t):
            changed.append(name)
    rep = analysis.representative
    for name, val in zip(REP_SLOTS, (rep.name, rep.relationship, rep.policyholder_name), strict=True):
        if val and session.memory.set(name, val.strip(), t):
            changed.append(name)
    h = analysis.case_hints
    pairs = (
        ("case_type", h.case_type), ("status_hint", h.status), ("month", h.month),
        ("year", h.year), ("case_id", h.case_id), ("free_text", h.free_text),
    )
    for name, val in pairs:
        if val is not None and session.memory.set(name, str(val), t):
            changed.append(name)
    if analysis.intent != "none" and session.memory.set("intent", analysis.intent, t):
        changed.append("intent")
    return changed


def _apply_corrections(session: Session, analysis: TurnAnalysis, changed: list[str]) -> None:
    t = session.turn
    for c in analysis.corrections:
        cur = session.memory.get(c.slot)
        was_verified = cur is not None and cur.status == SlotStatus.VERIFIED
        if session.memory.set(c.slot, c.new_value.strip(), t, overwrite_verified=True):
            changed.append(c.slot)
        if was_verified and session.verification.status == "verified":
            party_id = session.verification.party_id
            # the representative flag is sticky for the session; everything else about verification resets
            session.verification = Verification(
                declared_representative=session.verification.declared_representative)
            session.case = CaseState()  # a different party may verify next; its claims are re-resolved
            session.memory.reset_identity()
            # the earlier party's answers and replies are not the next party's: the summary, the earlier-
            # details rule, the email offer and the Writer's window look only past these fences
            # the fence sits before the correction message itself, so the Writer still sees what was just said
            session.fence_turn, session.transcript_fence = t, len(session.transcript) - 1
            session.phase = Phase.VERIFY_ID
            session.pending_ask = PendingAsk.NONE
            session.pending_draft = None  # a draft written for the earlier party is never sent to the next
            # a hand-off and a declined human offer belong to the party that gave them; the conduct counters
            # (off-topic, frustration, abuse) stay: a change of name is not a way round those rules
            session.escalation = Escalation()
            session.counters.human_declined = False
            session.log("verification_reset", slot=c.slot, party_id=party_id)
