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


def merge_analysis(session: Session, analysis: TurnAnalysis) -> list[str]:
    """Capture anything early. Only code advances phases; this only records what the caller said."""
    changed: list[str] = []
    t = session.turn
    # who is speaking, before anything else: an answer to an open identity question, a name or identifier
    # that belongs to someone else, a representative declaration after a verified policyholder, a change of
    # representative while a consent is pending or approved
    _confirm_answer(session, analysis)
    _identity_switches(session, analysis)
    _declaration_switch(session, analysis)
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


def _known_names(session: Session) -> list[str]:
    """The verified party's names on file plus the name the caller gave (a first name alone verifies too)."""
    slot = session.memory.value("full_name")
    return [*session.verification.names, *([slot] if slot else [])]


def _ask_identity(session: Session, analysis: TurnAnalysis, name: str) -> None:
    """Hold the new name on the session, not in the slots: the question names the caller as known so far."""
    session.pending_identity = name.strip()
    session.pending_ask = PendingAsk.IDENTITY_CONFIRM
    analysis.identity.full_name = analysis.representative.name = None


def _policyholder_switch(session: Session, analysis: TurnAnalysis, name: str) -> None:
    """Someone else is speaking: verification resets, the earlier party's identifiers go, and the name becomes
    theirs (a correction the identity loop then stores, so the trace shows it)."""
    _reset_verification(session, slot="full_name", party_id=session.verification.party_id, wipe=True)
    analysis.corrections.append(Correction(slot="full_name", new_value=name.strip()))


def _confirm_answer(session: Session, analysis: TurnAnalysis) -> None:
    """The answer to "is this still X?": yes keeps the session; no, or a name with nothing in common with the
    names on file, is someone else; anything else leaves the question open."""
    if session.pending_ask != PendingAsk.IDENTITY_CONFIRM:
        return
    r = analysis.requests
    name = analysis.identity.full_name or next(
        (c.new_value for c in analysis.corrections if c.slot == "full_name"), None)
    verdict = name_match(name, _known_names(session)) if name else None
    no = r.confirmation == "no" or verdict == "other"
    if r.confirmation == "yes" and not no:
        session.pending_ask, session.pending_identity = PendingAsk.NONE, None
        return
    if no:
        new_name = name if verdict in ("other", "partial") else session.pending_identity
        session.pending_identity = None
        analysis.corrections = [c for c in analysis.corrections if c.slot != "full_name"]
        if session.verification.declared_representative:
            _representative_reset(session)
        else:
            _policyholder_switch(session, analysis, new_name or session.pending_identity or "")
    # neither: the question stays open and is asked again


def _identity_switches(session: Session, analysis: TurnAnalysis) -> None:
    """Identity fields on a verified policyholder session, whether or not the Reader labelled them as a
    correction. A name: the same as one on file is a restatement; nothing in common is someone else and
    verification resets; partly the same is a question. A different date of birth or ID for a verified slot
    is someone else too."""
    v = session.verification
    if v.status != "verified" or v.role != "policyholder":
        return
    if session.pending_ask == PendingAsk.IDENTITY_CONFIRM:
        return
    names = [c.new_value for c in analysis.corrections if c.slot == "full_name"]
    if analysis.identity.full_name:
        names.append(analysis.identity.full_name)
    analysis.corrections = [c for c in analysis.corrections if c.slot != "full_name"]
    verdicts = {n: name_match(n, _known_names(session)) for n in names}
    if any(x == "other" for x in verdicts.values()):
        _policyholder_switch(session, analysis, next(n for n, x in verdicts.items() if x == "other"))
        return
    if any(x == "partial" for x in verdicts.values()):
        _ask_identity(session, analysis, next(n for n, x in verdicts.items() if x == "partial"))
        return
    labelled = {c.slot for c in analysis.corrections}
    for slot in ("dob", "id_last4"):
        given, cur = getattr(analysis.identity, slot), session.memory.get(slot)
        if given and slot not in labelled and cur is not None and cur.status == SlotStatus.VERIFIED:
            if not _same_person(slot, cur.value, str(given)):
                analysis.corrections.append(Correction(slot=slot, new_value=str(given).strip()))


def _declaration_switch(session: Session, analysis: TurnAnalysis) -> None:
    """A verified policyholder followed by a representative declaration. A caller who says they are a
    representative is someone else, whatever name they give ("this is Chen, Margaret Chen's son"); a caller
    of unknown role naming another policyholder is too, and naming one partly the same is a question. A
    policyholder who merely mentions a helper (caller_role stays policyholder) keeps the policyholder path."""
    v, rep = session.verification, analysis.representative
    if (v.status != "verified" or v.role != "policyholder" or analysis.caller_role == "policyholder"
            or session.pending_ask == PendingAsk.IDENTITY_CONFIRM):
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
    compared with the names as matched on file, never with a restated slot: the same is a restatement,
    nothing in common is someone else, partly the same ("Dave", "Tom Chen") is a question."""
    v, c = session.verification, session.consent
    if (not v.declared_representative or c.status not in ("pending", "approved")
            or session.pending_ask == PendingAsk.IDENTITY_CONFIRM):
        return
    held = [n for n in (c.representative_name, session.memory.value("rep_name")) if n]
    holder = [n for n in (c.policyholder_name, session.memory.value("rep_policyholder_name")) if n]
    names = [x.new_value for x in analysis.corrections if x.slot == "full_name"]
    names += [n for n in (analysis.representative.name, analysis.identity.full_name) if n]
    # the policyholder's own name in an identity field names the person consent is asked from, not a caller
    names = [n for n in names if name_match(n, holder) != "same"]
    verdicts = {n: name_match(n, held) for n in names}
    if any(x == "other" for x in verdicts.values()):
        analysis.corrections = [x for x in analysis.corrections if x.slot != "full_name"]
        _representative_reset(session)
    elif any(x == "partial" for x in verdicts.values()):
        analysis.corrections = [x for x in analysis.corrections if x.slot != "full_name"]
        _ask_identity(session, analysis, next(n for n, x in verdicts.items() if x == "partial"))


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
