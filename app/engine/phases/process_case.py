from datetime import date

from app.config import Settings
from app.data.models import Claim
from app.data.normalize import docs_match, fmt_date
from app.data.repos import Repos
from app.engine.briefs import HandlerResult
from app.engine.context import HUMAN_ASK, TurnContext
from app.engine.phases.resolve_intent import HINT_SLOT_NAMES, drop_stale_hints, hints_from_memory
from app.engine.state import PendingAsk, Phase, Session
from app.llm.schemas import ReplyBrief

ANYTHING_ELSE_ASK = "Is there anything else about this claim I can help with?"
NO_CHAT_UPLOAD = ("Say first that documents cannot be sent through this chat; they go through the "
                  "channels in submission_guidance.")
SUBMISSION_SHORT = ("Name documents_needed and the channel from submission_guidance in at most four "
                    "sentences, then offer the checklist of what each document must show instead of "
                    "reciting it.")
SUBMISSION_DETAIL = "Explain what each document must show, using the guidance facts and the topic fact."
DEADLINE_CAVEAT = ("Say in one sentence that the appeal deadline (appeal_deadline) has already passed as of "
                   "today, so sending documents now does not reopen the appeal by itself and a "
                   "representative would need to review the options.")
APPEAL_WORDS = ("appeal", "dispute", "reconsider", "contest")
CANNOT_WORDS = ("can't get", "cannot get", "can't obtain", "cannot obtain", "unable to get", "don't have",
                "do not have", "lost", "closed", "no longer")
SUBMISSION_TOPICS = ("submission_method", "file_format_requirements", "submission_timing",
                     "receipt_confirmation")
MUST_NOT = [
    "State only facts listed in allowed_facts; if something is not there, say you can't confirm it "
    "and offer a representative.",
    "Do not promise any outcome, payment, coverage decision or deadline extension.",
    "Do not repeat identifiers.",
]


def base_facts(claim: Claim, today: date) -> dict[str, str]:
    facts = {
        "today": fmt_date(today),
        "claim_id": claim.case_id, "claim_type": claim.case_type, "claim_status": claim.status,
        "claim_opened": fmt_date(claim.created_at), "claim_summary": claim.summary,
    }
    if claim.denial_reason:
        facts["denial_reason"] = claim.denial_reason
    if claim.documents_needed:
        facts["documents_needed"] = ", ".join(claim.documents_needed)
    if claim.appeal_deadline:
        facts["appeal_deadline"] = fmt_date(claim.appeal_deadline)
        facts["deadline_passed"] = "yes" if claim.appeal_deadline < today else "no"
    facts["allowed_max_amount"] = f"${claim.allowed_max_amount}"
    facts["expected_reimbursement_amount"] = f"${claim.expected_reimbursement_amount}"
    facts["net_pay"] = f"${claim.net_pay}"
    return facts


def hints_match(claim: Claim, session: Session, repos: Repos) -> bool:
    return bool(repos.claims.filter([claim], **hints_from_memory(session)))


def _key(name: str) -> str:
    return name.replace(" ", "_")


def handle(
    session: Session, ctx: TurnContext, repos: Repos, settings: Settings, today: date
) -> HandlerResult:
    a = ctx.analysis
    claim = repos.claims.get(session.case.selected_case_id)
    g = repos.guideline
    hint_changed = any(n in ctx.changed_slots for n in HINT_SLOT_NAMES)

    if a.requests.switch_claim or (hint_changed and not hints_match(claim, session, repos)):
        drop_stale_hints(session, ctx.changed_slots)
        session.case.selected_case_id = None
        session.phase = Phase.RESOLVE_INTENT
        session.pending_ask = PendingAsk.NONE
        ctx.anything_else_no = False  # a "no" here answered the old claim's ask, not the new claim's
        ctx.analysis.requests.switch_claim = False  # consumed, so re-entry this turn answers
        return HandlerResult(brief=ReplyBrief(phase=Phase.RESOLVE_INTENT.value, goal="Switch claim."),
                             advanced=True, needs_input=False)
    if ctx.anything_else_no or (a.requests.closing and a.intent == "none" and not a.question):
        session.phase = Phase.POST_PROCESS
        session.pending_ask = PendingAsk.NONE
        return HandlerResult(brief=ReplyBrief(phase=Phase.POST_PROCESS.value, goal="Wrap up."),
                             advanced=True, needs_input=False)

    facts = base_facts(claim, today)
    must_say: list[str] = []
    offer_human = False
    intent = a.intent if a.intent != "none" else (session.case.intent or "general_claim_question")
    topic = a.followup_topic if a.followup_topic != "none" else None
    q = (a.question or ctx.user_text).lower()
    docs = claim.documents_needed
    deadline_passed = facts.get("deadline_passed") == "yes"

    if not session.case.answered_once or intent in ("denial_question", "status_inquiry"):
        must_say.append("Give the claim's status using claim_status and claim_summary.")
        if claim.denial_reason:
            must_say.append("Explain the denial using denial_reason and list documents_needed.")
        if claim.appeal_deadline:
            tail = (" and that it has passed, so a representative would need to review any options."
                    if deadline_passed else ".")
            must_say.append("State appeal_deadline" + tail)

    wants_submission = (intent in ("document_submission", "next_steps")
                        and topic in (None, *SUBMISSION_TOPICS)) or topic in SUBMISSION_TOPICS
    if wants_submission and docs:
        for d in docs:
            if hit := g.document_guidance(d):
                facts[f"guidance_{_key(hit[0])}"] = hit[1]
        t = topic if topic in SUBMISSION_TOPICS else "submission_method"
        if txt := g.topic_text(t, claim):
            facts[f"topic_{t}"] = txt
        facts["submission_guidance"] = g.default_guidance()
        if ctg := g.case_type_guidance(claim.case_type):
            facts["case_type_guidance"] = ctg
        must_say.append(NO_CHAT_UPLOAD)
        must_say.append(SUBMISSION_DETAIL if t == "file_format_requirements" else SUBMISSION_SHORT)
        if deadline_passed:
            must_say.append(DEADLINE_CAVEAT)

    if topic == "processing_time_after_submission":
        if txt := g.topic_text(topic, claim):
            facts["topic_processing_time_after_submission"] = txt
            must_say.append("Explain the processing time using topic_processing_time_after_submission.")
            if deadline_passed:
                must_say.append(DEADLINE_CAVEAT)
        else:
            facts["fallback_guidance"] = g.fallback()
            must_say.append("Use fallback_guidance to set expectations.")

    cannot_get = topic == "missing_required_material_alternatives" or (
        any(w in q for w in CANNOT_WORDS) and any(docs_match(d, q) for d in docs))
    if cannot_get and docs:
        targets = [d for d in docs if docs_match(d, q)] or docs
        for d in targets:
            if hit := g.document_alternative(d):
                facts[f"alternative_{_key(hit[0])}"] = hit[1]
        facts["alternative_default"] = g.default_alternative()
        facts["human_review"] = g.human_review()
        must_say.append("Explain the alternatives using the alternative facts, then say a representative "
                        "can review manual options.")
        offer_human = True

    if deadline_passed and any(w in q for w in APPEAL_WORDS):
        must_say.append("Say the appeal deadline has passed and that a representative needs to review "
                        "whether a late appeal can be considered.")
        offer_human = True

    if not must_say:
        facts["fallback_guidance"] = g.fallback()
        must_say.append("Answer from the claim facts if they cover the question; otherwise use "
                        "fallback_guidance and say you can't confirm more here.")

    session.case.answered_once = True
    session.log("answered", intent=intent, topic=topic, facts=sorted(facts.keys()))
    if offer_human:
        ask, session.pending_ask = HUMAN_ASK, PendingAsk.HUMAN_OFFER
    else:
        ask, session.pending_ask = ANYTHING_ELSE_ASK, PendingAsk.ANYTHING_ELSE
    brief = ReplyBrief(phase=Phase.PROCESS_CASE.value,
                       goal="Answer the caller's question from grounded claim data only.",
                       allowed_facts=facts, must_say=must_say, must_not=MUST_NOT, ask=ask,
                       offer_human=offer_human)
    return HandlerResult(brief=brief)
