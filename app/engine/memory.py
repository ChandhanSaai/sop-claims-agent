import hashlib
from datetime import date

from app.data.models import Policyholder
from app.data.normalize import normalize_email, normalize_id4, normalize_name, normalize_phone, parse_dob
from app.data.repos import id_key
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
    Verification,
)
from app.llm.schemas import IdentityFields, RepresentativeInfo, TurnAnalysis


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
    for c in analysis.corrections:  # corrections first: the rest of the message is the corrected person's
        if session.memory.set(c.slot, c.new_value.strip(), t, overwrite_verified=True):
            changed.append(c.slot)
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
    """The same name, normalized (case, Latin accents, punctuation; any script)."""
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


def _dob_readings(value: str) -> set[date]:
    """Every date a written date of birth can mean: both orders when day and month could swap."""
    d, ambiguous = parse_dob(value)
    if d is None:
        return set()
    out = {d}
    if ambiguous:
        try:
            out.add(d.replace(month=d.day, day=d.month))
        except ValueError:
            pass
    return out


IDENTIFIERS = ("dob", "id_last4", "phone", "email", "policy_number")


def _forms(slot: str, value: str) -> set[str]:
    """Every normalized form a written identifier can mean (a date of birth: both orders when ambiguous)."""
    if slot == "dob":
        return {d.isoformat() for d in _dob_readings(value)}
    if slot == "id_last4":
        n = normalize_id4(value)
    elif slot == "phone":
        n = normalize_phone(value)
    elif slot == "email":
        n = normalize_email(value)
    else:
        n = id_key(value)
    return {n} if n else set()


def fingerprint(slot: str, form: str) -> str:
    return hashlib.sha256(f"{slot}:{form}".encode()).hexdigest()


def record_fingerprints(rec: Policyholder) -> dict[str, list[str]]:
    """Hashes of every identifier on file, aliases included: compared with, never shown."""
    values = {
        "dob": [rec.dob.isoformat()], "id_last4": [rec.id_last4],
        "phone": [rec.phone, *rec.phone_aliases], "email": [rec.email, *rec.email_aliases],
        "policy_number": [rec.policy_number],
    }
    return {slot: [fingerprint(slot, f) for v in vals for f in _forms(slot, str(v))]
            for slot, vals in values.items()}


def _same_value(session: Session, slot: str, given: str) -> bool:
    """An identifier that reads as the one the caller gave, or, when they never gave this one, as the one on
    file; an unreadable or ambiguous value that reads as neither is a question, never the same person."""
    forms = _forms(slot, given)
    if not forms:
        return False
    stored = session.memory.value(slot)
    if stored:
        return bool(forms & _forms(slot, stored))
    on_file = session.verification.fingerprints.get(slot, [])
    return any(fingerprint(slot, f) in on_file for f in forms)


def signals(session: Session, analysis: TurnAnalysis) -> list[str]:
    """Everything the Reader says about who is speaking that is not an exact restatement of what is known.
    On a policyholder session: a name other than the caller's, any representative detail, or a caller who
    says they are a representative (a helper mention is a representative detail too). On a representative
    session: a name other than the representative's or the policyholder's, a changed relationship, a caller
    who now says they are the policyholder. On a policyholder session also an identifier (date of birth,
    ID, phone, email, policy number) that reads as neither the one given before nor the one on file. Each
    item is the text that raised it."""
    v, rep, ident = session.verification, analysis.representative, analysis.identity
    known, holder = known_names(session), _holder_names(session)
    out: list[str] = []
    names = [ident.full_name, *[c.new_value for c in analysis.corrections if c.slot == "full_name"]]
    out += [n for n in names if n and not exact(n, known) and not exact(n, holder)]
    if v.declared_representative:
        if rep.name and not exact(rep.name, known):
            out.append(rep.name)
        if rep.policyholder_name and not exact(rep.policyholder_name, holder):
            out.append(rep.policyholder_name)
        if rep.relationship and not exact(rep.relationship, [session.memory.value("rep_relationship") or ""]):
            out.append(rep.relationship)
        if analysis.caller_role == "policyholder":
            out.append("the policyholder")
    elif rep.name or rep.relationship or rep.policyholder_name or analysis.caller_role == "representative":
        out.append(rep.name or rep.policyholder_name or "a representative")
    if v.declared_representative:  # a representative gives the policyholder's identifiers, never their own
        return out
    for slot in IDENTIFIERS:
        given = getattr(ident, slot) or next((c.new_value for c in analysis.corrections if c.slot == slot),
                                             None)
        if given and not _same_value(session, slot, str(given)):
            out.append(str(given))
    return out


def _restates_identity(session: Session, analysis: TurnAnalysis) -> bool:
    """The message carries identity content and every piece of it is something the gate compared and found
    exact: a name of the caller's own, or (on a policyholder session) an identifier that reads as theirs.
    Content the gate never compares (the policyholder's name or an identifier on a representative session,
    a relationship) is no answer."""
    v, rep, ident = session.verification, analysis.representative, analysis.identity
    known = known_names(session)
    names = [ident.full_name, *[c.new_value for c in analysis.corrections if c.slot == "full_name"]]
    if v.declared_representative and rep.name:
        names.append(rep.name)
    names = [n for n in names if n]
    idents = [] if v.declared_representative else [
        (slot, getattr(ident, slot) or next((c.new_value for c in analysis.corrections if c.slot == slot),
                                             None))
        for slot in IDENTIFIERS]
    idents = [(slot, g) for slot, g in idents if g]
    other = [c for c in analysis.corrections if c.slot not in ("full_name", *IDENTIFIERS)]
    if not names and not idents:
        return False
    if other or (v.declared_representative and (ident.dob or ident.id_last4 or ident.phone or ident.email
                                                  or ident.policy_number or rep.relationship
                                                  or rep.policyholder_name)):
        return False
    return (all(exact(n, known) for n in names)
            and all(_same_value(session, slot, str(g)) for slot, g in idents))


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


def identity_gate(session: Session, analysis: TurnAnalysis) -> None:
    """After verification, one rule for whoever the Reader says is speaking: an exact restatement of what is
    known continues; anything else (see `signals`) opens the question "is this still X?", which holds every
    claim detail until it is answered. Nothing is guessed from titles, initials, surnames, nicknames, roles
    or the labelling of a correction, and a flagged (injection) turn can only ask."""
    if not _speaking_verified(session):
        return
    found = signals(session, analysis)
    if found:
        _ask_identity(session, analysis, found[0])
    else:
        analysis.corrections = []  # exact restatements: nothing to store


def flagged_identity_check(session: Session, analysis: TurnAnalysis) -> None:
    """A turn the Reader flagged as an injection stores nothing and answers no question, but what it says
    about who is speaking can still lower trust: the question is raised wherever a plain turn would be."""
    if session.pending_identity is None:
        identity_gate(session, analysis)


def _confirm_answer(session: Session, analysis: TurnAnalysis) -> None:
    """The answer to "is this still X?": no is someone else; an answer that would itself open the question is
    no answer and is asked again; yes, or an exact restatement of the caller's own name or identifier, keeps
    the session and puts the displaced question again. No name from an answer is stored or bound."""
    q = session.pending_identity
    assert q is not None
    if analysis.requests.confirmation == "no":
        session.pending_identity = None
        switch(session)  # the message's own identity and representative details are the new caller's
        return
    if signals(session, analysis):
        _hold_identity(analysis)  # evidence of someone else beside a yes: asked again
        return
    if analysis.requests.confirmation == "yes" or _restates_identity(session, analysis):
        session.pending_identity, session.pending_ask = None, q.resume
        if q.resume in (PendingAsk.EMAIL_OFFER, PendingAsk.EMAIL_CONFIRM, PendingAsk.HUMAN_OFFER):
            session.reask = q.resume  # put again in fixed words, through the Writer and the guard
    _hold_identity(analysis)


def switch(session: Session) -> None:
    """Someone else is speaking: verification resets, identity and representative slots go together, the
    attempt count carries (one credential set never buys more guesses), and a representative's consent is
    dropped (the match cap and the one request per session carry too)."""
    v, c = session.verification, session.consent
    _reset_verification(session, party_id=v.party_id or c.party_id)
    if v.declared_representative:
        session.consent = Consent(match_attempts=c.match_attempts, requests=c.requests)


def caller_key(session: Session) -> str:
    """Who is verified or declared: a representative of a policyholder is not that policyholder."""
    v, c = session.verification, session.consent
    if v.declared_representative and c.party_id:
        return f"representative:{c.party_id}:{normalize_name(c.representative_name or '')}"
    return f"policyholder:{v.party_id}"


def _reset_verification(session: Session, *, party_id: str | None) -> None:
    """Whoever verifies next starts clean: claims, identifiers and representative details (wiped, so they can
    never verify the next caller as the earlier party), the pending question and draft, the hand-off and the
    declined human offer; the fences keep the earlier party's events and replies out of reach. The attempt
    count carries. The hand-off and the declined offer travel on the reset event, so the same caller gets
    them back. The conduct counters (off-topic, frustration, abuse) stay: a change of name is not a way
    round those rules."""
    old = session.verification
    session.log("verification_reset", slot="identity", party_id=party_id, caller=caller_key(session),
                escalation=session.escalation.model_dump(), human_declined=session.counters.human_declined)
    # the representative flag is sticky for the session; everything else about verification resets
    session.verification = Verification(declared_representative=old.declared_representative,
                                        attempts=old.attempts)
    session.case = CaseState()  # a different party may verify next; its claims are re-resolved
    for n in (*IDENTITY_SLOTS, *REP_SLOTS):
        session.memory.slots.pop(n, None)
    # the earlier party's answers and replies are not the next party's: the summary, the earlier-details
    # rule, the email offer and the Writer's window look only past these fences; the fence sits before the
    # answer itself, so the Writer still sees what was just said
    session.fence_turn, session.transcript_fence = session.turn, len(session.transcript) - 1
    session.phase = Phase.VERIFY_ID
    session.pending_ask = PendingAsk.NONE
    session.pending_identity = None
    session.reask = PendingAsk.NONE
    session.pending_draft = None  # a draft written for the earlier party is never sent to the next
    session.escalation = Escalation()
    session.counters.human_declined = False
