import hashlib
import json
from datetime import date

from app.config import Settings
from app.data.normalize import normalize_email, normalize_id4, normalize_name, normalize_phone, parse_dob
from app.data.repos import IDENTIFIERS, Repos
from app.engine.briefs import HandlerResult
from app.engine.context import HUMAN_ASK, TurnContext
from app.engine.phases.process_case import SUBMISSION_TOPICS
from app.engine.state import PendingAsk, Phase, Session
from app.llm.schemas import ReplyBrief

FIELD_LABELS = {
    "full_name": "your full name", "dob": "your date of birth", "phone": "the phone number on file",
    "email": "the email on file", "id_last4": "the last four digits of your SSN or national ID",
}
_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
GATE_WHY = (
    "Claim details are protected information, so I confirm identity before discussing them; "
    "that protects your claim and your personal information."
)
MEANWHILE = "Meanwhile, I can answer general questions about how claim documents are submitted."
GENERAL_SUBMISSION = ("Answer the general question about submitting claim documents from "
                      "submission_guidance, without reference to any claim.")
GENERIC_FAIL = (
    "I wasn't able to verify your identity with those details. Please check them and try again, "
    "or share different identifiers."
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


def _human_brief(session: Session, goal: str, must_say: list[str]) -> HandlerResult:
    offer = not session.counters.human_declined  # explained once; a declined offer is not repeated
    session.pending_ask = PendingAsk.HUMAN_OFFER if offer else PendingAsk.NONE
    brief = ReplyBrief(phase=Phase.VERIFY_ID.value, goal=goal, must_say=must_say, must_not=BASE_MUST_NOT,
                       offer_human=offer, ask=HUMAN_ASK if offer else None)
    return HandlerResult(brief=brief)


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
    if v.status == "exhausted":
        return _human_brief(
            session,
            "Verification was not completed; offer a representative, answer only general questions.",
            ["Identity verification wasn't completed in this conversation, so I can't discuss claim "
             "details here.",
             "A representative can verify your identity another way."],
        )
    if a.caller_role == "representative" or a.representative.name:
        v.declared_representative = True
    if v.declared_representative and a.caller_role != "policyholder":
        return _human_brief(
            session,
            "Explain representative access without confirming any record.",
            ["Claim details can be discussed with the policyholder, or with an authorized representative "
             "once the policyholder's consent is on record.",
             "A representative can arrange that consent."],
        )

    provided = {n: val for n in IDENTIFIERS if (val := session.memory.value(n))}
    if "dob" in provided:
        d, ambiguous = parse_dob(provided["dob"])
        if d is None or ambiguous:  # unreadable, or day and month could swap: re-ask, not an attempt
            session.pending_ask = PendingAsk.DOB_FORMAT
            brief = ReplyBrief(
                phase=Phase.VERIFY_ID.value,
                goal="Re-ask the date of birth with the month spelled out.",
                must_say=["I want to make sure I read your date of birth correctly."],
                must_not=BASE_MUST_NOT,
                ask="Could you give your date of birth with the month spelled out, "
                    "for example 4 July 1990?",
            )
            return HandlerResult(brief=brief)

    hints_noted = any(
        session.memory.value(n)
        for n in ("case_type", "status_hint", "month", "year", "case_id", "free_text", "intent")
    )

    if len(provided) < settings.verify_min_fields:
        must_say = ([NOTED] if hints_noted else []) + [identity_ask(provided, settings.verify_min_fields)]
        options: list[str] = []
        if ctx.tone == "de_escalate" and session.counters.gate_explanations < 2:
            must_say += [GATE_WHY, MEANWHILE]
            options = ALT_OPTIONS
            session.counters.gate_explanations += 1
        session.pending_ask = PendingAsk.IDENTITY_FIELDS
        brief = ReplyBrief(
            phase=Phase.VERIFY_ID.value,
            goal="Collect the remaining identifiers without confirming that any record exists.",
            must_say=must_say, must_not=BASE_MUST_NOT, options=options,
            ask="Which of those can you share?",
        )
        return HandlerResult(brief=brief)

    # Minimum identifiers on hand: this is one attempt, unless nothing changed since the last one.
    fp = _fingerprint(provided)
    if fp == v.last_fingerprint:
        session.pending_ask = PendingAsk.IDENTITY_FIELDS
        brief = ReplyBrief(phase=Phase.VERIFY_ID.value, goal="Ask for corrected or different identifiers.",
                           must_say=[GENERIC_FAIL], must_not=BASE_MUST_NOT,
                           ask="Could you re-check the details or share different identifiers?")
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
        ask="Could you re-check the details or share different identifiers?",
    )
    return HandlerResult(brief=brief)
