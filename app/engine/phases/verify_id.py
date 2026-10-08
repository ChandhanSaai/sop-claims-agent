import hashlib
import json
from datetime import date

from app.config import Settings
from app.data.normalize import parse_dob
from app.data.repos import IDENTIFIERS, Repos
from app.engine.briefs import HandlerResult
from app.engine.context import HUMAN_ASK, TurnContext
from app.engine.state import PendingAsk, Phase, Session
from app.llm.schemas import ReplyBrief

IDENTITY_ASK = (
    "To verify your identity, please share at least three of these: your full name, date of birth, "
    "the phone number on file, the email on file, or the last four digits of your SSN or national ID. "
    "Your policy number also helps me find your record."
)
GATE_WHY = (
    "Claim details are protected information, so I confirm identity before discussing them; "
    "that protects your claim and your personal information."
)
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
ONE_SENTENCE_ACK = "Acknowledge the caller's frustration in one specific sentence, then move on."


def _fingerprint(provided: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(sorted(provided.items())).encode()).hexdigest()


def _human_brief(session: Session, goal: str, must_say: list[str]) -> HandlerResult:
    session.pending_ask = PendingAsk.HUMAN_OFFER
    brief = ReplyBrief(phase=Phase.VERIFY_ID.value, goal=goal, must_say=must_say, must_not=BASE_MUST_NOT,
                       offer_human=True, ask=HUMAN_ASK)
    return HandlerResult(brief=brief)


def handle(
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
        return _human_brief(
            session,
            "Explain representative access without confirming any record.",
            ["Claim details can be discussed with the policyholder, or with an authorized representative "
             "once the policyholder's consent is on record.",
             "A representative can arrange that consent."],
        )

    provided = {n: s.value for n in IDENTIFIERS if (s := session.memory.get(n)) and s.value}
    if "dob" in provided:
        d, ambiguous = parse_dob(provided["dob"])
        if d is None or ambiguous:
            del provided["dob"]
            if ambiguous:
                session.pending_ask = PendingAsk.DOB_FORMAT
                brief = ReplyBrief(
                    phase=Phase.VERIFY_ID.value,
                    goal="Re-ask the date of birth with the month spelled out.",
                    must_say=["I want to make sure I read your date of birth correctly."],
                    must_not=BASE_MUST_NOT,
                    ask="Could you give your date of birth with the month spelled out, "
                        "for example 15 March 1985?",
                )
                return HandlerResult(brief=brief)

    hints_noted = any(
        session.memory.value(n)
        for n in ("case_type", "status_hint", "month", "year", "case_id", "free_text", "intent")
    )

    if len(provided) < settings.verify_min_fields:
        must_say = ([NOTED] if hints_noted else []) + [IDENTITY_ASK]
        options: list[str] = []
        if ctx.tone == "de_escalate" and session.counters.gate_explanations < 2:
            must_say.append(GATE_WHY)
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
    passes = [r for r in candidates
              if repos.policyholders.verify(r, provided, min_fields=settings.verify_min_fields,
                                            require_strong=settings.verify_require_strong_field).passed]
    if len(passes) == 1:
        rec = passes[0]
        v.status, v.party_id, v.role = "verified", rec.party_id, "policyholder"
        session.memory.mark_verified(provided.keys())
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
