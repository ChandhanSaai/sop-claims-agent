from datetime import date

from app.config import Settings
from app.data.normalize import mask_email
from app.data.repos import Repos
from app.engine.briefs import HandlerResult
from app.engine.context import TurnContext
from app.engine.state import PendingAsk, Phase, Session
from app.engine.summary import build_summary
from app.llm.schemas import ReplyBrief

GOODBYE = "Say goodbye and that the assistant remains available if anything else comes up."


def handle(
    session: Session, ctx: TurnContext, repos: Repos, settings: Settings, today: date
) -> HandlerResult:
    a = ctx.analysis
    c = session.counters
    holder = repos.policyholders.get(session.verification.party_id)
    masked = mask_email(holder.email)
    phase = Phase.POST_PROCESS.value

    if not c.email_offered:
        c.email_offered = True
        session.pending_ask = PendingAsk.EMAIL_OFFER
        brief = ReplyBrief(phase=phase, goal="Offer an email summary once, default no.",
                           allowed_facts={"email_on_file_masked": masked},
                           must_say=["Offer to send a summary of this conversation (what was discussed, "
                                     "the claim status and next steps) to the email on file, shown as "
                                     "email_on_file_masked."],
                           must_not=["Do not ask for an email address.", "Do not show the full address."],
                           ask="Would you like me to send that summary?")
        return HandlerResult(brief=brief)

    if ctx.email_yes:
        session.pending_draft = build_summary(session, repos, today)
        session.pending_ask = PendingAsk.EMAIL_CONFIRM
        brief = ReplyBrief(phase=phase, goal="Show the draft and ask whether to send it.",
                           allowed_facts={"email_on_file_masked": masked},
                           must_say=["Say the draft is shown below and ask whether to send it to the email "
                                     "on file."],
                           must_not=["Do not restate the draft's contents yourself."],
                           ask="Shall I send it?", verbatim=session.pending_draft)
        return HandlerResult(brief=brief)

    if ctx.email_confirm_yes and session.pending_draft:
        rec = repos.outbox.send(holder.email, "Summary of your claims support conversation",
                                session.pending_draft)
        session.log("email_sent", email_id=rec.id, to_masked=rec.to_masked)
        session.pending_draft = None
        session.pending_ask = PendingAsk.NONE
        brief = ReplyBrief(phase=phase, goal="Confirm the email was sent and close.",
                           allowed_facts={"email_reference": rec.id, "email_on_file_masked": masked},
                           must_say=["The summary was sent to the email on file; give email_reference.",
                                     GOODBYE])
        return HandlerResult(brief=brief)

    if ctx.email_no or ctx.email_confirm_no:
        session.pending_draft = None
        session.pending_ask = PendingAsk.NONE
        brief = ReplyBrief(phase=phase, goal="Close without sending anything.",
                           must_say=["Nothing will be sent.", GOODBYE])
        return HandlerResult(brief=brief)

    new_question = a.intent != "none" or bool(a.question) or any(
        n in ctx.changed_slots for n in ("case_type", "status_hint", "month", "year", "case_id"))
    if new_question:
        session.phase = Phase.RESOLVE_INTENT
        session.pending_ask = PendingAsk.NONE
        return HandlerResult(brief=ReplyBrief(phase=Phase.RESOLVE_INTENT.value,
                                              goal="New question after goodbye."),
                             advanced=True, needs_input=False)

    session.pending_ask = PendingAsk.NONE
    return HandlerResult(brief=ReplyBrief(phase=phase, goal="Short goodbye.", must_say=[GOODBYE]))
