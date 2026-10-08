from app.config import Settings
from app.engine.context import HUMAN_ASK, TurnContext
from app.engine.state import PendingAsk, Session
from app.llm.schemas import ReplyBrief


def _acknowledgment_seed(session: Session) -> str:
    if session.memory.value("status_hint") == "denied":
        return "Waiting to hear why a claim was denied is frustrating, and I want to get you an answer."
    return "I can tell this has been frustrating, and I want to get it sorted out for you."


def pass1(session: Session, ctx: TurnContext, settings: Settings) -> None:
    """Before the phase chain: update counters and state from the caller's message."""
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


def pass2(session: Session, ctx: TurnContext, brief: ReplyBrief) -> ReplyBrief:
    """After the chain: overlay tone, acknowledgment and the human offer onto the merged brief."""
    update: dict = {"must_say": list(brief.must_say) + ctx.extra_must_say}
    if ctx.tone != "neutral":
        update["tone"] = ctx.tone
    if ctx.acknowledge and not brief.acknowledge:
        update["acknowledge"] = ctx.acknowledge
    if ctx.offer_human and not brief.offer_human:
        update["offer_human"] = True
        update["ask"] = HUMAN_ASK
        session.pending_ask = PendingAsk.HUMAN_OFFER
    return brief.model_copy(update=update)
