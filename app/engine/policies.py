from app.config import Settings
from app.engine.context import HUMAN_ASK, TurnContext
from app.engine.state import Escalation, PendingAsk, Session
from app.llm.schemas import ReplyBrief

META_LINE = ("The caller asked about the assistant itself: say plainly that this is an automated assistant, "
             "that identity verification protects their claim information, and that details from this "
             "conversation are used only to handle their request.")
MIXED_LINE = ("The caller also asked about something outside claims support; say in one short sentence "
              "that you can't help with that part here.")
EARLIER_DETAILS_STAND = ("Do not revisit, correct or disclaim the claim details given earlier in this "
                         "conversation; they stand. This reply covers only what this brief asks for.")
NEW_DEVELOPMENT = ("Open with the new development as plain news. Do not say you are correcting, updating or "
                   "taking back anything you said earlier: those replies were right when given.")
STATE_CHANGE_EVENTS = ("verification_reset", "consent_timed_out")
SCOPE_LINE = ("This assistant handles questions about your claims with us: status, denials, documents, "
              "deadlines and next steps.")
BOUNDARY_LINE = ("I'm glad to keep helping with your claim, and I need this conversation to stay respectful "
                 "so that I can.")
CLOSE_LINE = "This conversation hasn't stayed respectful, so I'm ending it here."
ABUSE_CLOSE_AT = 2  # the spec: one boundary statement, then the conversation ends


def _acknowledgment_seed(session: Session) -> str:
    if session.memory.value("status_hint") == "denied":
        return "Waiting to hear why a claim was denied is frustrating, and I want to get you an answer."
    return "I can tell this has been frustrating, and I want to get it sorted out for you."


def escalate(session: Session, reason: str) -> str:
    """Issue a reference and log the hand-off packet once. The session keeps its phase and gates."""
    if not session.escalation.requested:
        ref = "ESC-" + session.id[:6].upper()
        session.escalation = Escalation(requested=True, reference=ref, reason=reason)
        session.log("escalated", reference=ref, reason=reason, packet={
            "phase": session.phase.value, "verified": session.verification.status,
            "role": session.verification.role,
            "case_id": session.case.selected_case_id, "intent": session.case.intent,
            "off_topic": session.counters.off_topic,
            "frustration_streak": session.counters.frustration_streak,
        })
    return session.escalation.reference


def escalation_brief(session: Session, first: bool, lead: list[str] | None = None) -> ReplyBrief:
    must_say = list(lead or [])
    must_say.append("A representative will follow up on this conversation." if first
                    else "A representative has already been asked to follow up on this conversation.")
    must_say.append("Give the handoff_reference and say you remain available for claim questions, "
                    "with the same verification rules.")
    return ReplyBrief(phase=session.phase.value, goal="Confirm the hand-off and give the reference.",
                      allowed_facts={"handoff_reference": session.escalation.reference}, must_say=must_say,
                      must_not=["Do not disclose any claim details beyond what was already allowed."])


def closing_brief(session: Session, first: bool) -> ReplyBrief:
    """End the conversation after repeated abuse: calm, short, the human route, nothing else."""
    followup = ("A representative will follow up on this conversation." if first
                else "A representative has already been asked to follow up on this conversation.")
    return ReplyBrief(phase=session.phase.value, goal="End the conversation calmly and give the human route.",
                      tone="de_escalate",
                      must_say=[CLOSE_LINE, followup, "Give the handoff_reference for that follow-up."],
                      allowed_facts={"handoff_reference": session.escalation.reference},
                      must_not=["Do not answer any question in this message.",
                                "Do not lecture, moralize or apologize; two or three sentences.",
                                "Do not disclose any claim details."])


def _can_help_with(session: Session) -> str:
    if session.verification.status == "verified":
        return "the status of this claim, what documents are needed, how to submit them and what happens next"
    return ("verifying your identity so we can look at your claim, and general questions about how claim "
            "documents are submitted")


def _decline_brief(session: Session, n: int, settings: Settings) -> ReplyBrief:
    must_say = [SCOPE_LINE, f"Offer what you can help with: {_can_help_with(session)}."]
    if session.last_brief and session.last_brief.ask and n == 1 and session.pending_ask != PendingAsk.NONE:
        must_say.append(f"Then return to the open question: {session.last_brief.ask}")
    goal = ("Decline the off-topic request briefly" + (" in different words than before" if n > 1 else "")
            + " and restate scope.")
    # A declined human offer suppresses the ladder's offer for the rest of the session.
    offer = n == settings.offtopic_human_offer_at and not session.counters.human_declined
    brief = ReplyBrief(phase=session.phase.value, goal=goal, must_say=must_say,
                       must_not=["Do not answer the off-topic question.",
                                 "Do not sound robotic; vary the wording."],
                       offer_human=offer, ask=HUMAN_ASK if offer else None)
    if offer:
        session.pending_ask = PendingAsk.HUMAN_OFFER
    return brief


def pass1(session: Session, ctx: TurnContext, settings: Settings) -> None:
    """Before the phase chain: update counters and state from the caller's message;
    may short-circuit the chain."""
    a = ctx.analysis
    heat = max(a.affect.frustration, a.affect.anger)
    if heat >= 2 or a.affect.refusal:
        ctx.tone = "de_escalate"
        session.counters.frustration_streak += 1
        if session.counters.frustration_streak == 1:
            ctx.acknowledge = _acknowledgment_seed(session)
    else:
        session.counters.frustration_streak = 0
    if session.counters.frustration_streak >= 2:
        ctx.offer_human = True

    if a.injection_suspected:
        session.log("injection_suspected")
    if a.affect.abusive:
        session.counters.abusive += 1
        ctx.tone = "de_escalate"
        if session.counters.abusive >= ABUSE_CLOSE_AT:
            first = not session.escalation.requested
            escalate(session, "abusive caller")
            session.closed = True
            session.pending_ask = PendingAsk.NONE
            ctx.offer_human = False
            ctx.acknowledge = None  # the closing reply never opens with the frustration acknowledgment
            session.log("conversation_closed", reason="abuse", reference=session.escalation.reference)
            ctx.policy_brief = closing_brief(session, first)
            return
        ctx.extra_must_say.append(BOUNDARY_LINE)
    if a.requests.wants_human or ctx.human_yes:
        first = not session.escalation.requested
        escalate(session, "caller asked for a representative")
        session.pending_ask = PendingAsk.NONE
        ctx.offer_human = False
        ctx.policy_brief = escalation_brief(session, first)
        return
    if ctx.human_no:
        session.pending_ask = PendingAsk.NONE
        session.counters.human_declined = True

    off_topic = a.scope == "out_of_scope" or a.injection_suspected
    if off_topic:
        session.counters.off_topic += 1
        n = session.counters.off_topic
        if session.escalation.requested or (n > settings.offtopic_human_offer_at
                                            and not session.counters.human_declined):
            first = not session.escalation.requested
            escalate(session, "repeated off-topic requests")
            if session.pending_ask == PendingAsk.HUMAN_OFFER:  # any other open question stays open
                session.pending_ask = PendingAsk.NONE
            ctx.offer_human = False
            brief = escalation_brief(session, first,
                                     lead=[SCOPE_LINE, "Decline in different words than before."])
            brief.must_not.append("Do not answer the off-topic question.")
            ctx.policy_brief = brief
        else:
            ctx.policy_brief = _decline_brief(session, n, settings)
        return
    session.counters.off_topic = 0
    if a.scope == "meta":
        ctx.extra_must_say.append(META_LINE)
    elif a.scope == "mixed":
        ctx.extra_must_say.append(MIXED_LINE)


def pass2(session: Session, ctx: TurnContext, brief: ReplyBrief) -> ReplyBrief:
    """After the chain: overlay tone, acknowledgment and the human offer onto the merged brief."""
    update: dict = {"must_say": list(brief.must_say) + ctx.extra_must_say}
    must_not = list(brief.must_not)
    has_claim_facts = any(k == "claim_id" or k.startswith("option_") for k in brief.allowed_facts)
    if not has_claim_facts and any(e.type == "answered" for e in session.fenced_events()):
        must_not.append(EARLIER_DETAILS_STAND)  # a goodbye, offer or decline after claim details were given
    if any(e.turn == session.turn and e.type in STATE_CHANGE_EVENTS for e in session.events):
        must_not.append(NEW_DEVELOPMENT)
    if len(must_not) != len(brief.must_not):
        update["must_not"] = must_not
    if ctx.tone != "neutral":
        update["tone"] = ctx.tone
    if ctx.acknowledge and not brief.acknowledge:
        update["acknowledge"] = ctx.acknowledge
    if (ctx.offer_human and not brief.offer_human and not session.escalation.requested
            and not session.counters.human_declined):
        update["offer_human"] = True
        update["ask"] = HUMAN_ASK
        session.pending_ask = PendingAsk.HUMAN_OFFER
    return brief.model_copy(update=update)
