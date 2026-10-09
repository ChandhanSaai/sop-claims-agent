from app.data.normalize import normalize_id4, normalize_name, parse_dob
from app.engine.state import (
    IDENTITY_SLOTS,
    REP_SLOTS,
    CaseState,
    Consent,
    Escalation,
    PendingAsk,
    Phase,
    Session,
    SlotStatus,
    Verification,
)
from app.llm.schemas import Correction, TurnAnalysis

# a different value for one of these after verification is a different person, whatever the Reader called it;
# a phone or email can legitimately differ (another contact) and is never read as a correction
PERSON_SLOTS = ("full_name", "dob", "id_last4")
_TITLES = {"mr", "mrs", "ms", "miss", "mx", "dr", "sr", "sra", "srta", "herr", "frau", "mme", "mlle", "m"}


def merge_analysis(session: Session, analysis: TurnAnalysis) -> list[str]:
    """Capture anything early. Only code advances phases; this only records what the caller said."""
    changed: list[str] = []
    t = session.turn
    _implicit_corrections(session, analysis)
    _representative_switch(session, analysis)
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


def _same_person(slot: str, verified: str, given: str) -> bool:
    """A restatement of the verified value in another form is not a correction: a title, a first name alone,
    a middle name added, a date in another form; an unreadable or ambiguous date is re-asked, not reset."""
    if slot == "full_name":
        a = set(normalize_name(verified).split()) - _TITLES
        b = set(normalize_name(given).split()) - _TITLES
        return not b or a <= b or b <= a
    if slot == "dob":
        d, ambiguous = parse_dob(given)
        return d is None or ambiguous or d == parse_dob(verified)[0]
    return normalize_id4(given) in (None, normalize_id4(verified))


def _implicit_corrections(session: Session, analysis: TurnAnalysis) -> None:
    """\"This is actually Ma Tian, born ...\" read as identity fields rather than corrections, or \"this is
    David Chen, calling for my mother\" read as a representative declaration, still names someone else:
    verification must reset, not keep answering the earlier party's questions. A verified policyholder who
    merely mentions a helper (caller_role stays policyholder) keeps the policyholder path."""
    labelled = {c.slot for c in analysis.corrections}
    for slot in PERSON_SLOTS:
        given, cur = getattr(analysis.identity, slot), session.memory.get(slot)
        if given and slot not in labelled and cur is not None and cur.status == SlotStatus.VERIFIED:
            if not _same_person(slot, cur.value, str(given)):
                analysis.corrections.append(Correction(slot=slot, new_value=str(given).strip()))
    rep, cur = analysis.representative.name, session.memory.get("full_name")
    if (rep and analysis.caller_role == "representative" and "full_name" not in labelled
            and session.verification.role == "policyholder" and cur is not None
            and cur.status == SlotStatus.VERIFIED and not _same_person("full_name", cur.value, rep)):
        analysis.corrections.append(Correction(slot="full_name", new_value=rep.strip()))


def _representative_switch(session: Session, analysis: TurnAnalysis) -> None:
    """A declared representative who turns out to be someone else while a consent is pending or approved: the
    consent was for the name given before, so it is dropped with the fence and the representative flow starts
    again for the new name. The representative flag stays (a later claim to be the policyholder does not
    reopen the policyholder path) and so does the match cap."""
    v, c = session.verification, session.consent
    if not v.declared_representative or c.status not in ("pending", "approved"):
        return
    held = [n for n in (c.representative_name, session.memory.value("rep_name")) if n]
    named = [x.new_value for x in analysis.corrections if x.slot == "full_name"]
    if analysis.representative.name:
        named.append(analysis.representative.name)
    if not named or all(any(_same_person("full_name", h, n) for h in held) for n in named):
        return
    _reset_verification(session, slot="rep_name", party_id=c.party_id)
    session.consent = Consent(match_attempts=c.match_attempts)
    for n in REP_SLOTS:  # the new representative's details are captured from this message on
        session.memory.slots.pop(n, None)


def _reset_verification(session: Session, *, slot: str, party_id: str | None) -> None:
    """Whoever verifies next starts clean: claims, identity status, the pending question and draft, the
    hand-off and the declined human offer; the fences keep the earlier party's events and replies out of
    reach. The hand-off and the declined offer travel on the reset event, so a same-party re-verification
    gets them back. The conduct counters (off-topic, frustration, abuse) stay: a change of name is not a way
    round those rules."""
    session.log("verification_reset", slot=slot, party_id=party_id,
                escalation=session.escalation.model_dump(), human_declined=session.counters.human_declined)
    # the representative flag is sticky for the session; everything else about verification resets
    session.verification = Verification(declared_representative=session.verification.declared_representative)
    session.case = CaseState()  # a different party may verify next; its claims are re-resolved
    session.memory.reset_identity()
    # the earlier party's answers and replies are not the next party's: the summary, the earlier-details
    # rule, the email offer and the Writer's window look only past these fences; the fence sits before the
    # correction message itself, so the Writer still sees what was just said
    session.fence_turn, session.transcript_fence = session.turn, len(session.transcript) - 1
    session.phase = Phase.VERIFY_ID
    session.pending_ask = PendingAsk.NONE
    session.pending_draft = None  # a draft written for the earlier party is never sent to the next
    session.escalation = Escalation()
    session.counters.human_declined = False


def _apply_corrections(session: Session, analysis: TurnAnalysis, changed: list[str]) -> None:
    t = session.turn
    for c in analysis.corrections:
        cur = session.memory.get(c.slot)
        was_verified = cur is not None and cur.status == SlotStatus.VERIFIED
        if session.memory.set(c.slot, c.new_value.strip(), t, overwrite_verified=True):
            changed.append(c.slot)
        if was_verified and session.verification.status == "verified":
            _reset_verification(session, slot=c.slot, party_id=session.verification.party_id)
