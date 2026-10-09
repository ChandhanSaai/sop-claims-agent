from datetime import date

from app.engine.context import resolve_pending
from app.engine.memory import merge_analysis
from app.engine.state import Phase, Session, Verification
from app.llm.schemas import TurnAnalysis

TODAY = date(2026, 10, 7)


def verified_session(repos, party_id: str = "P9") -> Session:
    s = Session.new()
    s.verification = Verification(status="verified", party_id=party_id, role="policyholder")
    s.phase = Phase.RESOLVE_INTENT
    return s


def turn(session, repos, settings, handler, user_text: str = "", today: date = TODAY, **analysis_fields):
    analysis = TurnAnalysis.model_validate(analysis_fields)
    session.turn += 1
    ctx = resolve_pending(session, analysis, user_text)
    ctx.changed_slots = merge_analysis(session, analysis)
    return handle_with(session, ctx, repos, settings, handler, today)


def handle_with(session, ctx, repos, settings, handler, today: date = TODAY):
    return handler(session, ctx, repos, settings, today)
