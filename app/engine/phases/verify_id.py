import hashlib
import json
from datetime import date

from app.config import Settings
from app.data.normalize import normalize_email, normalize_id4, normalize_name, normalize_phone, parse_dob
from app.data.repos import IDENTIFIERS, Repos
from app.engine.briefs import HandlerResult
from app.engine.context import HUMAN_ASK, TurnContext
from app.engine.phases.post_process import GOODBYE
from app.engine.phases.process_case import SUBMISSION_TOPICS
from app.engine.state import HINT_SLOTS, IDENTITY_SLOTS, REP_SLOTS, PendingAsk, Phase, Session
from app.llm.schemas import ReplyBrief

FIELD_LABELS = {
    "full_name": "your full name", "dob": "your date of birth", "phone": "the phone number on file",
    "email": "the email on file", "id_last4": "the last four digits of your SSN or national ID",
}
_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
FULL_NAME_NEEDED = "I'll need your full name as it appears on the policy."
FULL_NAME_ASK = "Could I have your full name, please?"
GATE_WHY = (
    "Claim details are protected information, so I confirm identity before discussing them; "
    "that protects your claim and your personal information."
)
MEANWHILE = "Meanwhile, I can answer general questions about how claim documents are submitted."
GENERAL_SUBMISSION = ("Answer the general question about submitting claim documents from "
                      "submission_guidance, without reference to any claim.")
GENERIC_FAIL = (
    "I wasn't able to verify your identity with those details."
)
NOTED = "I've noted what you're calling about and will look at it as soon as verification is complete."
BASE_MUST_NOT = [
    "Do not confirm or deny that any policy, claim or record exists.",
    "Do not mention any claim details.",
    "Do not repeat identifiers the caller gave (dates of birth, phone numbers, emails, ID digits, "
    "policy numbers).",
]
ALT_OPTIONS = [
    "date of birth plus the last four digits of your SSN or national ID",
    "the phone number and email address on file",
    "a representative who can verify your identity another way",
]
_NORMALIZERS = {"full_name": normalize_name, "dob": lambda s: parse_dob(s)[0], "phone": normalize_phone,
                "email": normalize_email, "id_last4": normalize_id4}
REP_LABELS = {"rep_name": "your full name", "rep_relationship": "your relationship to the policyholder",
              "rep_policyholder_name": "the policyholder's full name"}
REP_CALL = "This conversation is being handled as a representative call."
# one sentence for every no-match, whichever name failed: it must not reveal whether the policyholder exists
NO_AUTHORIZATION = "I couldn't confirm an authorization on file for that representative and policyholder."
NO_CONTACT = "Do not name or describe the policyholder's contact details."
CONSENT_REQUESTED = (
    "A consent request has been sent to the policyholder's contact on file; once they approve it, I can "
    "discuss claim details with you as their authorized representative."
)
POA_REVIEW = (
    "A power of attorney has to be reviewed by a person who can check the documents, so I can't discuss "
    "claim details on that basis here; I can still answer general questions about how claim documents are "
    "submitted."
)
CONSENT_PENDING = "The policyholder's consent is still pending."
NOT_PENDING = "Do not say the consent is still pending: it timed out in this conversation."
CONSENT_TIMED_OUT = (
    "The policyholder's consent could not be obtained in this conversation, so I can't discuss claim details "
    "with you as their representative here; I can still answer general questions about how claim documents "
    "are submitted."
)
CONSENT_FACT = (
    "Consent was received from the policyholder; you are verified as their authorized representative. "
    "Give the consent_reference."
)
MEANWHILE_ASK = "Is there a general question I can help with in the meantime?"


def _fingerprint(provided: dict[str, str]) -> str:
    """Hash of the normalized identifiers, so a format-only restatement ("March 15, 1985" vs "1985-03-15")
    is not a new attempt. A value that does not normalize is hashed as given; str(date) is ISO."""
    canonical = sorted((n, str(_NORMALIZERS[n](v) or v)) for n, v in provided.items())
    return hashlib.sha256(json.dumps(canonical).encode()).hexdigest()


def identity_ask(provided: dict[str, str], min_fields: int) -> str:
    """Ask for exactly what is still missing. The wording depends only on what the caller said, never on
    whether a lookup found anything."""
    need = min_fields - len(provided)
    labels = [FIELD_LABELS[n] for n in IDENTIFIERS if n not in provided]
    items = (", ".join(labels[:-1]) + f", or {labels[-1]}") if len(labels) > 1 else labels[0]
    return (f"To verify your identity, please share at least {_WORDS.get(need, str(need))}"
            f"{' more' if provided else ''} of these: {items}. "
            "Your policy number also helps me find your record.")


def _human_brief(session: Session, goal: str, must_say: list[str],
                 extra_must_not: tuple[str, ...] = ()) -> HandlerResult:
    # explained once; a declined offer is not repeated, nor is one after an escalation already happened
    offer = not (session.counters.human_declined or session.escalation.requested)
    session.pending_ask = PendingAsk.HUMAN_OFFER if offer else PendingAsk.NONE
    brief = ReplyBrief(phase=Phase.VERIFY_ID.value, goal=goal, must_say=must_say,
                       must_not=[*BASE_MUST_NOT, *extra_must_not], offer_human=offer,
                       ask=HUMAN_ASK if offer else None)
    return HandlerResult(brief=brief)


def _new_party_cleanup(session: Session, party_id: str) -> None:
    """Someone other than the party before the verification reset verified: the hints given before the
    reset are not theirs, and the summary offer is theirs to get."""
    before = next((e.data["party_id"] for e in reversed(session.events)
                   if e.type == "verification_reset"), None)
    if before and before != party_id:
        for n in HINT_SLOTS:
            if (slot := session.memory.get(n)) and slot.source_turn < session.fence_turn:
                del session.memory.slots[n]
        session.counters.email_offered = False


def _approve(session: Session) -> HandlerResult:
    v, c = session.verification, session.consent
    _new_party_cleanup(session, c.party_id)
    v.status, v.party_id, v.role = "verified", c.party_id, "representative"
    session.memory.mark_verified(REP_SLOTS)
    session.phase = Phase.RESOLVE_INTENT
    session.pending_ask = PendingAsk.NONE
    brief = ReplyBrief(phase=Phase.RESOLVE_INTENT.value, goal="Consent received; continue.",
                       must_say=[CONSENT_FACT], must_not=["Do not repeat identifiers."])
    return HandlerResult(brief=brief, advanced=True, needs_input=False, transition_fact=CONSENT_FACT,
                         transition_facts={"consent_reference": c.consent_id})


def _representative(session: Session, ctx: TurnContext, repos: Repos, settings: Settings,
                    hints_noted: bool) -> HandlerResult:
    """Sub-flow driven by session.consent: every status comes from a tool result, consent is requested once
    per session and polled once per turn, and the policyholder's identifiers are never looked up here."""
    c = session.consent
    phase = Phase.VERIFY_ID.value
    lead = [REP_CALL] if ctx.analysis.caller_role == "policyholder" else []
    if c.status == "pending":
        status = repos.consent.poll(c.consent_id)
        c.polls += 1
        if status == "approved":
            c.status = "approved"
            session.log("consent_approved", consent_id=c.consent_id, party_id=c.party_id)
        elif status == "timed_out":
            c.status = "timed_out"
            session.log("consent_timed_out", consent_id=c.consent_id, polls=c.polls)
        else:
            session.pending_ask = PendingAsk.CONSENT_WAIT
            brief = ReplyBrief(phase=phase, goal="Consent is still pending; say what the caller can do.",
                               must_say=lead + [CONSENT_PENDING, MEANWHILE], must_not=BASE_MUST_NOT,
                               ask=MEANWHILE_ASK)
            return HandlerResult(brief=brief)
    if c.status == "approved":
        return _approve(session)
    if c.status == "timed_out":  # never re-requested
        return _human_brief(session, "Consent was not obtained; general information only; offer a human.",
                            lead + [CONSENT_TIMED_OUT], extra_must_not=(NOT_PENDING,))
    # spec 7: a claimed power of attorney goes to a person for document review, before any match or consent
    relationship = normalize_name(session.memory.value("rep_relationship") or "")
    if relationship == "poa" or "power of attorney" in relationship or "attorney in fact" in relationship:
        if not any(e.type == "poa_claimed" for e in session.events):
            session.log("poa_claimed")
        return _human_brief(session, "A claimed power of attorney needs document review by a person; "
                                     "general information only.", lead + [POA_REVIEW])
    rep = {n: session.memory.value(n) for n in REP_SLOTS}
    missing = [REP_LABELS[n] for n in REP_SLOTS if not rep[n]]
    if missing:
        items = (", ".join(missing[:-1]) + f" and {missing[-1]}") if len(missing) > 1 else missing[0]
        session.pending_ask = PendingAsk.IDENTITY_FIELDS
        ask_line = f"To handle this as a representative call, I need {items}."
        brief = ReplyBrief(
            phase=phase, goal="Collect the representative details without confirming any record exists.",
            must_say=lead + ([NOTED] if hints_noted else []) + [ask_line], must_not=BASE_MUST_NOT,
            ask="Could you share those?",
        )
        return HandlerResult(brief=brief)
    fingerprint = "|".join(normalize_name(x) for x in rep.values())
    # a restated triple is not matched again, and after verify_max_attempts failed pairs nothing is: the
    # same sentence either way, so the cap cannot be told from a miss
    if fingerprint != c.last_match and c.match_attempts < settings.verify_max_attempts:
        c.last_match = fingerprint
        match = repos.representatives.match(rep["rep_name"], rep["rep_policyholder_name"])
        if match is not None:
            cid = repos.consent.request(match.buyer_party_id, match.rep_name, session.scenario)
            c.status, c.consent_id, c.party_id = "pending", cid, match.buyer_party_id
            c.representative_name = match.rep_name
            session.log("consent_requested", consent_id=cid, scenario=session.scenario)
            session.pending_ask = PendingAsk.CONSENT_WAIT
            brief = ReplyBrief(
                phase=phase, goal="Say a consent request went to the policyholder; nothing about any claim.",
                must_say=lead + ([NOTED] if hints_noted else []) + [CONSENT_REQUESTED, MEANWHILE],
                must_not=BASE_MUST_NOT + [NO_CONTACT], ask=MEANWHILE_ASK,
            )
            return HandlerResult(brief=brief)
        c.match_attempts += 1
        session.log("rep_match_failed", attempts=c.match_attempts)
    return _human_brief(session, "Say no authorization is on file, without saying which name did not match.",
                        lead + [NO_AUTHORIZATION])


def handle(
    session: Session, ctx: TurnContext, repos: Repos, settings: Settings, today: date
) -> HandlerResult:
    result = _handle(session, ctx, repos, settings, today)
    a = ctx.analysis
    # spec 7: a general process question is answered from the guideline's default guidance before verification
    if not result.advanced and (a.intent == "document_submission" or a.followup_topic in SUBMISSION_TOPICS):
        result.brief.allowed_facts["submission_guidance"] = repos.guideline.default_guidance()
        result.brief.must_say.append(GENERAL_SUBMISSION)
    return result


def _handle(
    session: Session, ctx: TurnContext, repos: Repos, settings: Settings, today: date
) -> HandlerResult:
    v = session.verification
    a = ctx.analysis
    if (a.requests.closing and a.intent == "none" and not a.question
            and not any(n in ctx.changed_slots for n in IDENTITY_SLOTS)):  # spec 7: goodbye, nothing changes
        brief = ReplyBrief(phase=Phase.VERIFY_ID.value, goal="Short goodbye; verification stays as it is.",
                           must_say=[GOODBYE], must_not=BASE_MUST_NOT)
        return HandlerResult(brief=brief)
    if v.status == "exhausted":
        return _human_brief(
            session,
            "Verification was not completed; offer a representative, answer only general questions.",
            ["Identity verification wasn't completed in this conversation, so I can't discuss claim "
             "details here.",
             "A representative can verify your identity another way."],
        )
    hints_noted = any(  # hints from before a verification reset are not this caller's
        (slot := session.memory.get(n)) and slot.source_turn >= session.fence_turn
        for n in ("case_type", "status_hint", "month", "year", "case_id", "free_text", "intent")
    )
    rep = a.representative
    # a policyholder who merely mentions a helper keeps the policyholder path; once set, the flag wins
    if a.caller_role != "policyholder" and (
            a.caller_role == "representative" or rep.name or rep.relationship or rep.policyholder_name):
        v.declared_representative = True
    if v.declared_representative:
        return _representative(session, ctx, repos, settings, hints_noted)

    provided = {n: val for n in IDENTIFIERS if (val := session.memory.value(n))}
    # a single word is a first name: incomplete rather than wrong, so it costs no attempt
    first_name_only = "full_name" in provided and len(normalize_name(provided["full_name"]).split()) < 2
    if first_name_only:
        provided.pop("full_name")
    if "dob" in provided:
        d, ambiguous = parse_dob(provided["dob"])
        if d is None or ambiguous:  # unreadable, or day and month could swap: re-ask, not an attempt
            session.pending_ask = PendingAsk.DOB_FORMAT
            brief = ReplyBrief(
                phase=Phase.VERIFY_ID.value,
                goal="Re-ask the date of birth with the month as a word.",
                must_say=["I want to make sure I have your date of birth right."],
                must_not=BASE_MUST_NOT,
                ask="Could you give it with the month written as a word, for example 4 July 1990?",
            )
            return HandlerResult(brief=brief)

    if len(provided) < settings.verify_min_fields:
        must_say = [NOTED] if hints_noted else []
        ask = "Which of those can you share?"
        if first_name_only and len(provided) == settings.verify_min_fields - 1:
            must_say.append(FULL_NAME_NEEDED)  # the full name alone completes the set
            ask = FULL_NAME_ASK
        else:
            must_say += ([FULL_NAME_NEEDED] if first_name_only else []) + [
                identity_ask(provided, settings.verify_min_fields)]
        options: list[str] = []
        if ctx.tone == "de_escalate" and session.counters.gate_explanations < 2:
            must_say += [GATE_WHY, MEANWHILE]
            options = ALT_OPTIONS
            session.counters.gate_explanations += 1
        session.pending_ask = PendingAsk.IDENTITY_FIELDS
        brief = ReplyBrief(
            phase=Phase.VERIFY_ID.value,
            goal="Collect the remaining identifiers without confirming that any record exists.",
            must_say=must_say, must_not=BASE_MUST_NOT, options=options, ask=ask,
        )
        return HandlerResult(brief=brief)

    # Minimum identifiers on hand: this is one attempt, unless nothing changed since the last one.
    fp = _fingerprint(provided)
    if fp == v.last_fingerprint:
        session.pending_ask = PendingAsk.IDENTITY_FIELDS
        brief = ReplyBrief(phase=Phase.VERIFY_ID.value, goal="Ask for corrected or different identifiers.",
                           must_say=[GENERIC_FAIL], must_not=BASE_MUST_NOT,
                           ask="Could you double-check them, or give me a different detail instead?")
        return HandlerResult(brief=brief)
    v.last_fingerprint = fp
    candidates = repos.policyholders.find(
        policy_number=session.memory.value("policy_number"), phone=provided.get("phone"),
        email=provided.get("email"), name=provided.get("full_name"),
    )
    checks = [(r, repos.policyholders.verify(r, provided, min_fields=settings.verify_min_fields,
                                             require_strong=settings.verify_require_strong_field))
              for r in candidates]
    passes = [(r, res) for r, res in checks if res.passed]
    if len(passes) == 1:
        rec, result = passes[0]
        _new_party_cleanup(session, rec.party_id)
        v.status, v.party_id, v.role = "verified", rec.party_id, "policyholder"
        session.memory.mark_verified(result.matched)  # a wrong extra identifier stays provisional
        session.log("verified", party_id=rec.party_id, fields=len(provided))
        session.phase = Phase.RESOLVE_INTENT
        session.pending_ask = PendingAsk.NONE
        fact = "Identity verification is complete."
        brief = ReplyBrief(phase=Phase.RESOLVE_INTENT.value, goal="Verification complete; continue.",
                           must_say=[fact], must_not=["Do not repeat identifiers."])
        return HandlerResult(brief=brief, advanced=True, needs_input=False, transition_fact=fact)

    v.attempts += 1
    session.log("verify_failed", attempts=v.attempts)
    if v.attempts >= settings.verify_max_attempts:
        v.status = "exhausted"
        return _human_brief(
            session,
            "Verification failed for the last time; stop asking and offer a representative.",
            [GENERIC_FAIL,
             "I can't keep trying in this conversation, but a representative can verify your identity "
             "another way."],
        )
    session.pending_ask = PendingAsk.IDENTITY_FIELDS
    brief = ReplyBrief(
        phase=Phase.VERIFY_ID.value,
        goal="Report that verification did not succeed, without saying which detail failed.",
        must_say=[GENERIC_FAIL],
        must_not=BASE_MUST_NOT + ["Do not say which detail did not match."],
        ask="Could you double-check them, or give me a different detail instead?",
    )
    return HandlerResult(brief=brief)
