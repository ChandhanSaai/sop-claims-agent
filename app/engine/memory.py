from app.data.normalize import normalize_id4, normalize_name, parse_dob
from app.engine.state import (
    IDENTITY_SLOTS,
    REP_SLOTS,
    CaseState,
    Consent,
    Escalation,
    IdentityQuestion,
    PendingAsk,
    Phase,
    Session,
    SlotStatus,
    Verification,
)
from app.llm.schemas import Correction, IdentityFields, RepresentativeInfo, TurnAnalysis


def merge_analysis(session: Session, analysis: TurnAnalysis) -> list[str]:
    """Capture anything early. Only code advances phases; this only records what the caller said."""
    changed: list[str] = []
    t = session.turn
    # who is speaking, before anything else: the answer to an open identity question, or the one rule that
    # applies after verification
    if session.pending_identity is not None:
        _confirm_answer(session, analysis)
    else:
        identity_gate(session, analysis)
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


def exact(given: str, names: list[str]) -> bool:
    """The same name, normalized (case, accents, punctuation; any script)."""
    g = normalize_name(given)
    return bool(g) and any(g == normalize_name(k) for k in names if k)


def _speaking_verified(session: Session) -> bool:
    """Someone verified, or a declared representative whose consent is pending or approved, is speaking."""
    v, c = session.verification, session.consent
    return v.status == "verified" or (v.declared_representative and c.status in ("pending", "approved"))


def known_names(session: Session) -> list[str]:
    """The names the current caller is known by: a representative by the name the consent was requested for
    and the name they gave; a policyholder by the names on file (record name and aliases) plus the name they
    gave, since a first name alone verifies too."""
    v, c = session.verification, session.consent
    if v.declared_representative:
        return [n for n in (c.representative_name, session.memory.value("rep_name")) if n]
    slot = session.memory.value("full_name")
    return [*v.names, *([slot] if slot else [])]


def _holder_names(session: Session) -> list[str]:
    """On a representative call, the policyholder's names name the person consent is asked from, not the
    caller."""
    if not session.verification.declared_representative:
        return []
    c = session.consent
    return [n for n in (c.policyholder_name, session.memory.value("rep_policyholder_name")) if n]


def _names_given(analysis: TurnAnalysis, *, with_rep: bool) -> list[str]:
    names = [c.new_value for c in analysis.corrections if c.slot == "full_name"]
    if analysis.identity.full_name:
        names.append(analysis.identity.full_name)
    if with_rep:
        names += [n for n in (analysis.representative.name, analysis.representative.policyholder_name) if n]
    return names


def _hold_identity(analysis: TurnAnalysis) -> None:
    """Nothing about identity from this message is stored: no field, correction or representative detail."""
    analysis.identity = IdentityFields()
    analysis.corrections = []
    analysis.representative = RepresentativeInfo()


def _ask_identity(session: Session, analysis: TurnAnalysis, candidate: str) -> None:
    """Open the question "is this still X?". It names the caller as they gave their name, remembers the
    question it displaces so a yes can put it again, and holds everything identity-related in this message."""
    who_slot = "rep_name" if session.verification.declared_representative else "full_name"
    resume = session.pending_ask
    declined = resume == PendingAsk.HUMAN_OFFER and analysis.requests.confirmation == "no"
    if resume == PendingAsk.IDENTITY_CONFIRM or declined:  # a declined offer is not put again
        resume = PendingAsk.NONE
    session.pending_identity = IdentityQuestion(
        candidate=candidate.strip(), who=session.memory.value(who_slot) or "the person verified earlier",
        resume=resume)
    session.pending_ask = PendingAsk.IDENTITY_CONFIRM
    _hold_identity(analysis)


def identity_gate(session: Session, analysis: TurnAnalysis, *, flagged: bool = False) -> None:
    """After verification, one rule for anything the Reader says about who is speaking. A name (identity
    field, labelled correction, representative name) that exactly matches a name the caller is known by is a
    restatement. Any other name, any representative detail, or a caller_role of representative opens the
    question "is this still X?", which holds every claim detail until it is answered; nothing is guessed from
    titles, initials, surnames or nicknames. A policyholder who mentions a helper (caller_role policyholder)
    keeps their path. A different date of birth or ID for a verified slot is someone else outright. A flagged
    (injection) turn can only raise the question."""
    if not _speaking_verified(session):
        return
    v, rep = session.verification, analysis.representative
    helper = analysis.caller_role == "policyholder" and not v.declared_representative
    names = [n for n in _names_given(analysis, with_rep=not helper) if not exact(n, _holder_names(session))]
    strange = [n for n in names if not exact(n, known_names(session))]
    # a declaration counts on a policyholder session; a declared representative restating the role, a
    # relationship or the policyholder is who they said they were, and only a new name changes that
    details = bool(rep.name or rep.relationship or rep.policyholder_name)
    declares = not v.declared_representative and (analysis.caller_role == "representative"
                                                 or (not helper and details))
    if strange or declares:
        _ask_identity(session, analysis, strange[0] if strange else (rep.name or rep.policyholder_name or ""))
        return
    analysis.corrections = [c for c in analysis.corrections if c.slot != "full_name"]  # exact: restated
    if flagged or v.role != "policyholder":
        return
    labelled = {c.slot for c in analysis.corrections}
    for slot in ("dob", "id_last4"):
        given, cur = getattr(analysis.identity, slot), session.memory.get(slot)
        if given and slot not in labelled and cur is not None and cur.status == SlotStatus.VERIFIED:
            if not _same_person(slot, cur.value, str(given)):
                switch(session)
                analysis.corrections.append(Correction(slot=slot, new_value=str(given).strip()))
                return


def flagged_identity_check(session: Session, analysis: TurnAnalysis) -> None:
    """A turn the Reader flagged as an injection stores nothing and answers no question, but a name or role in
    it can still lower trust: the question is raised and holds every claim detail until it is answered."""
    if session.pending_identity is None:
        identity_gate(session, analysis, flagged=True)


def _same_person(slot: str, verified: str, given: str) -> bool:
    """A date of birth or ID restated in another form is not a correction; an unreadable or ambiguous date is
    re-asked by the phase, not reset."""
    if slot == "dob":
        d, ambiguous = parse_dob(given)
        return d is None or ambiguous or d == parse_dob(verified)[0]
    return normalize_id4(given) in (None, normalize_id4(verified))


def _confirm_answer(session: Session, analysis: TurnAnalysis) -> None:
    """The answer to "is this still X?": yes, or the caller's own exact name, keeps the session and puts the
    displaced question again; no is someone else; anything else leaves the question open. No name from the
    answer is stored as the caller's."""
    q = session.pending_identity
    assert q is not None
    r = analysis.requests
    names = [n for n in _names_given(analysis, with_rep=True) if not exact(n, _holder_names(session))]
    exact_all = bool(names) and all(exact(n, known_names(session)) for n in names)
    no = r.confirmation == "no"
    if (r.confirmation == "yes" or exact_all) and not no:
        session.pending_identity, session.pending_ask = None, q.resume
        # the displaced question is put again in fixed words, through the Writer and the guard
        if q.resume in (PendingAsk.EMAIL_OFFER, PendingAsk.EMAIL_CONFIRM, PendingAsk.HUMAN_OFFER):
            session.reask = q.resume
        _hold_identity(analysis)
        return
    if no:
        session.pending_identity = None
        switch(session)  # the message's own identity and representative details then belong to the new caller
        return
    _hold_identity(analysis)  # neither: the question stays open and is asked again


def switch(session: Session) -> None:
    """Someone else is speaking: verification resets, identity and representative slots go together, the
    attempt count carries (one credential set never buys more guesses), and a representative's consent is
    dropped (the match cap and the one request per session carry too)."""
    v, c = session.verification, session.consent
    _reset_verification(session, slot="identity", party_id=v.party_id or c.party_id, wipe=True)
    if v.declared_representative:
        session.consent = Consent(match_attempts=c.match_attempts, requests=c.requests)


def caller_key(session: Session) -> str:
    """Who is verified or declared: a representative of a policyholder is not that policyholder."""
    v, c = session.verification, session.consent
    if v.declared_representative and c.party_id:
        return f"representative:{c.party_id}:{normalize_name(c.representative_name or '')}"
    return f"policyholder:{v.party_id}"


def _reset_verification(session: Session, *, slot: str, party_id: str | None, wipe: bool = False) -> None:
    """Whoever verifies next starts clean: claims, identity status, the pending question and draft, the
    hand-off and the declined human offer; the fences keep the earlier party's events and replies out of
    reach. When someone else is speaking (wipe) the earlier party's identifiers and representative details go
    too, so they can never verify the next caller as that party; a typo correction keeps them, provisional,
    for the same caller to re-verify with. The attempt count carries. The hand-off and the declined offer
    travel on the reset event, so the same caller gets them back. The conduct counters (off-topic,
    frustration, abuse) stay: a change of name is not a way round those rules."""
    old = session.verification
    session.log("verification_reset", slot=slot, party_id=party_id, caller=caller_key(session),
                escalation=session.escalation.model_dump(), human_declined=session.counters.human_declined)
    # the representative flag is sticky for the session; everything else about verification resets
    session.verification = Verification(declared_representative=old.declared_representative,
                                        attempts=old.attempts)
    session.case = CaseState()  # a different party may verify next; its claims are re-resolved
    if wipe:
        for n in (*IDENTITY_SLOTS, *REP_SLOTS):
            session.memory.slots.pop(n, None)
    else:
        session.memory.reset_identity()
    # the earlier party's answers and replies are not the next party's: the summary, the earlier-details
    # rule, the email offer and the Writer's window look only past these fences; the fence sits before the
    # correction message itself, so the Writer still sees what was just said
    session.fence_turn, session.transcript_fence = session.turn, len(session.transcript) - 1
    session.phase = Phase.VERIFY_ID
    session.pending_ask = PendingAsk.NONE
    session.pending_identity = None
    session.reask = PendingAsk.NONE
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
