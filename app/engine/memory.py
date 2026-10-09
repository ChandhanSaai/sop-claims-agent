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
from app.llm.schemas import Correction, IdentityFields, TurnAnalysis


def merge_analysis(session: Session, analysis: TurnAnalysis) -> list[str]:
    """Capture anything early. Only code advances phases; this only records what the caller said."""
    changed: list[str] = []
    t = session.turn
    # who is speaking, before anything else: an answer to an open identity question, a name or identifier
    # that belongs to someone else, a representative declaration after a verified policyholder, a change of
    # representative while a consent is pending or approved
    _confirm_answer(session, analysis)
    _declaration_switch(session, analysis)
    _identity_switches(session, analysis)
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


def flagged_identity_check(session: Session, analysis: TurnAnalysis) -> None:
    """A turn the Reader flagged as an injection stores nothing, but a name in it that is not the caller's can
    still lower trust: the question is raised and holds every claim detail until it is answered."""
    if session.pending_identity is not None or not _speaking_verified(session):
        return
    for n in _names_given(analysis, with_rep=analysis.caller_role != "policyholder"):
        if name_match(n, _known_names(session)) != "same":
            _ask_identity(session, analysis, n)
            return


def name_match(given: str, known: list[str]) -> str:
    """How a name relates to the names on file: "same" when, normalized, it equals one of them; "partial" when
    it shares a word, an initial or a three-letter start with one ("Mrs. Chen", "Margaret C.", "Maggie
    Chen", "Dave" for David) or is a single word, which cannot name a new person on its own; "other" when
    nothing is in common. Partial is a question for the caller, never a guess either way."""
    b = set(normalize_name(given).split())
    if not b:
        return "same"
    sets = [set(normalize_name(k).split()) for k in known if k]
    if any(a == b for a in sets):
        return "same"
    for a in sets:
        for x in b:
            for y in a:
                initial = (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y))
                if x == y or initial or x[:3] == y[:3]:
                    return "partial"
    return "partial" if len(b) == 1 else "other"


def _same_person(slot: str, verified: str, given: str) -> bool:
    """A date of birth or ID restated in another form is not a correction; an unreadable or ambiguous date is
    re-asked by the phase, not reset."""
    if slot == "dob":
        d, ambiguous = parse_dob(given)
        return d is None or ambiguous or d == parse_dob(verified)[0]
    return normalize_id4(given) in (None, normalize_id4(verified))


def _speaking_verified(session: Session) -> bool:
    """Someone verified, or a declared representative whose consent is pending or approved, is speaking."""
    v, c = session.verification, session.consent
    return v.status == "verified" or (v.declared_representative and c.status in ("pending", "approved"))


def _known_names(session: Session) -> list[str]:
    """The names the current caller is known by: a representative by the name the consent was requested for
    and the name they gave; a policyholder by the names on file plus the name they gave (a first name alone
    verifies too)."""
    v, c = session.verification, session.consent
    if v.declared_representative:
        names = [n for n in (c.representative_name, session.memory.value("rep_name")) if n]
    else:
        slot = session.memory.value("full_name")
        names = [*v.names, *([slot] if slot else [])]
    return names + session.confirmed_names


def _names_given(analysis: TurnAnalysis, *, with_rep: bool) -> list[str]:
    """The names this message gives for the caller: identity fields and labelled corrections, plus the
    representative name where the caller is the representative (a policyholder's helper is not the caller)."""
    names = [c.new_value for c in analysis.corrections if c.slot == "full_name"]
    if analysis.identity.full_name:
        names.append(analysis.identity.full_name)
    if with_rep and analysis.representative.name:
        names.append(analysis.representative.name)
    return names


def _hold_identity(analysis: TurnAnalysis) -> None:
    """Nothing about identity from this message is stored: no field, no correction, no representative name."""
    analysis.identity = IdentityFields()
    analysis.corrections = []
    analysis.representative.name = None


def _ask_identity(session: Session, analysis: TurnAnalysis, name: str) -> None:
    """Open the question "is this still X?". It names the caller as known so far, remembers the question it
    displaces so a yes can resume it, and holds everything identity-related in this message."""
    who_slot = "rep_name" if session.verification.declared_representative else "full_name"
    resume = session.pending_ask
    declined = resume == PendingAsk.HUMAN_OFFER and analysis.requests.confirmation == "no"
    if resume == PendingAsk.IDENTITY_CONFIRM or declined:  # a declined offer is not put again
        resume = PendingAsk.NONE
    session.pending_identity = IdentityQuestion(
        candidate=name.strip(), who=session.memory.value(who_slot) or "the person verified earlier",
        resume=resume)
    session.pending_ask = PendingAsk.IDENTITY_CONFIRM
    _hold_identity(analysis)


def _policyholder_switch(session: Session, analysis: TurnAnalysis, name: str) -> None:
    """Someone else is speaking: verification resets, the earlier party's identifiers go, and the name becomes
    theirs (a correction the identity loop then stores, so the trace shows it)."""
    _reset_verification(session, slot="full_name", party_id=session.verification.party_id, wipe=True)
    analysis.corrections = [c for c in analysis.corrections if c.slot != "full_name"]
    analysis.corrections.append(Correction(slot="full_name", new_value=name.strip()))


def _confirm_answer(session: Session, analysis: TurnAnalysis) -> None:
    """The answer to "is this still X?": yes keeps the session and resumes the question it displaced; no, or a
    name with nothing in common with the caller's names, is someone else; anything else leaves the question
    open. Whatever the outcome, no name from the answer is stored as the caller's."""
    q = session.pending_identity
    if q is None:
        return
    rep = session.verification.declared_representative
    names = _names_given(analysis, with_rep=rep)
    if rep:  # the policyholder's name names the person consent is asked from, not the caller
        c = session.consent
        holder = [n for n in (c.policyholder_name, session.memory.value("rep_policyholder_name")) if n]
        names = [n for n in names if name_match(n, holder) != "same"]
    verdicts = [name_match(n, _known_names(session)) for n in names]
    # a labelled correction that is not the caller's name is an explicit no
    corrected = [c.new_value for c in analysis.corrections if c.slot == "full_name"]
    insists = any(name_match(n, _known_names(session)) != "same" for n in corrected)
    r = analysis.requests
    no = r.confirmation == "no" or "other" in verdicts or insists
    # an exact restatement of the caller's own name answers yes by itself
    yes = r.confirmation == "yes" or ("same" in verdicts and "partial" not in verdicts)
    if yes and not no:
        session.pending_identity, session.pending_ask = None, q.resume
        session.confirmed_names.append(q.candidate)  # the nickname is theirs from now on
        # the displaced question is put again in fixed words, through the Writer and the guard
        if q.resume in (PendingAsk.EMAIL_OFFER, PendingAsk.EMAIL_CONFIRM, PendingAsk.HUMAN_OFFER):
            session.reask = q.resume
        _hold_identity(analysis)
        return
    if no:
        new_name = next((n for n, v in zip(names, verdicts, strict=True) if v == "other"), None)
        new_name = new_name or (names[0] if names else q.candidate)
        session.pending_identity = None
        if session.verification.declared_representative:
            _representative_reset(session)
        else:
            _policyholder_switch(session, analysis, new_name)
        return
    _hold_identity(analysis)  # neither: the question stays open and is asked again


def _identity_switches(session: Session, analysis: TurnAnalysis) -> None:
    """Identity fields on a verified policyholder session, whether or not the Reader labelled them as a
    correction. A name: the same as one on file is a restatement; nothing in common is someone else and
    verification resets; partly the same is a question. A different date of birth or ID for a verified slot
    is someone else too: the reset wipes the earlier identifiers, a labelled typo correction keeps them."""
    v = session.verification
    if v.status != "verified" or v.role != "policyholder" or session.pending_identity is not None:
        return
    names = _names_given(analysis, with_rep=False)
    verdicts = {n: name_match(n, _known_names(session)) for n in names}
    if any(x == "other" for x in verdicts.values()):
        _policyholder_switch(session, analysis, next(n for n, x in verdicts.items() if x == "other"))
        return
    if any(x == "partial" for x in verdicts.values()):
        _ask_identity(session, analysis, next(n for n, x in verdicts.items() if x == "partial"))
        return
    analysis.corrections = [c for c in analysis.corrections if c.slot != "full_name"]  # same: restated
    labelled = {c.slot for c in analysis.corrections}
    for slot in ("dob", "id_last4"):
        given, cur = getattr(analysis.identity, slot), session.memory.get(slot)
        if given and slot not in labelled and cur is not None and cur.status == SlotStatus.VERIFIED:
            if not _same_person(slot, cur.value, str(given)):
                _reset_verification(session, slot=slot, party_id=v.party_id, wipe=True)
                analysis.corrections.append(Correction(slot=slot, new_value=str(given).strip()))
                return


def _declaration_switch(session: Session, analysis: TurnAnalysis) -> None:
    """A verified policyholder followed by a representative declaration. A caller who says they are a
    representative is someone else, whatever name they give ("this is Chen, Margaret Chen's son"); a caller
    of unknown role naming another policyholder is too, and naming one partly the same is a question. A
    policyholder who merely mentions a helper (caller_role stays policyholder) keeps the policyholder path."""
    v, rep = session.verification, analysis.representative
    if (v.status != "verified" or v.role != "policyholder" or analysis.caller_role == "policyholder"
            or session.pending_identity is not None):
        return
    if analysis.caller_role == "representative" and (rep.name or rep.relationship or rep.policyholder_name):
        _reset_verification(session, slot="representative", party_id=v.party_id, wipe=True)
    elif rep.policyholder_name:
        verdict = name_match(rep.policyholder_name, _known_names(session))
        if verdict == "other":
            _reset_verification(session, slot="representative", party_id=v.party_id, wipe=True)
        elif verdict == "partial":
            _ask_identity(session, analysis, rep.policyholder_name)


def _representative_reset(session: Session) -> None:
    """The consent was for the person who declared before: it is dropped with the fence and the representative
    flow starts again. The flag stays (a later claim to be the policyholder does not reopen the policyholder
    path), and so do the match cap and the one request per session."""
    c = session.consent
    _reset_verification(session, slot="rep_name", party_id=c.party_id, wipe=True)
    session.consent = Consent(match_attempts=c.match_attempts, requests=c.requests)
    for n in REP_SLOTS:  # the new representative's details are captured from this message on
        session.memory.slots.pop(n, None)


def _representative_switch(session: Session, analysis: TurnAnalysis) -> None:
    """A declared representative who may be someone else while a consent is pending or approved. Names are
    compared with the name the consent was requested for, never with a restated slot: the same is a
    restatement, nothing in common is someone else, partly the same ("Dave", "Tom Chen") is a question."""
    v, c = session.verification, session.consent
    if (not v.declared_representative or c.status not in ("pending", "approved")
            or session.pending_identity is not None):
        return
    holder = [n for n in (c.policyholder_name, session.memory.value("rep_policyholder_name")) if n]
    # the policyholder's own name in an identity field names the person consent is asked from, not a caller
    names = [n for n in _names_given(analysis, with_rep=True) if name_match(n, holder) != "same"]
    verdicts = {n: name_match(n, _known_names(session)) for n in names}
    if any(x == "other" for x in verdicts.values()):
        analysis.corrections = [x for x in analysis.corrections if x.slot != "full_name"]
        _representative_reset(session)
    elif any(x == "partial" for x in verdicts.values()):
        _ask_identity(session, analysis, next(n for n, x in verdicts.items() if x == "partial"))
    else:
        analysis.corrections = [x for x in analysis.corrections if x.slot != "full_name"]  # restated


def caller_key(session: Session) -> str:
    """Who is verified or declared: a representative of a policyholder is not that policyholder."""
    v, c = session.verification, session.consent
    if v.declared_representative and c.party_id:
        return f"representative:{c.party_id}:{normalize_name(c.representative_name or '')}"
    return f"policyholder:{v.party_id}"


def _reset_verification(session: Session, *, slot: str, party_id: str | None, wipe: bool = False) -> None:
    """Whoever verifies next starts clean: claims, identity status, the pending question and draft, the
    hand-off and the declined human offer; the fences keep the earlier party's events and replies out of
    reach. When someone else is speaking (wipe) the earlier party's identifiers go too, so they can never
    verify the next caller as that party; a typo correction keeps them, provisional, for the same caller to
    re-verify with. The hand-off and the declined offer travel on the reset event, so the same caller gets
    them back. The conduct counters (off-topic, frustration, abuse) stay: a change of name is not a way
    round those rules."""
    session.log("verification_reset", slot=slot, party_id=party_id, caller=caller_key(session),
                escalation=session.escalation.model_dump(), human_declined=session.counters.human_declined)
    # the representative flag is sticky for the session; everything else about verification resets
    session.verification = Verification(declared_representative=session.verification.declared_representative)
    session.case = CaseState()  # a different party may verify next; its claims are re-resolved
    if wipe:
        for n in IDENTITY_SLOTS:
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
    session.confirmed_names = []
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
