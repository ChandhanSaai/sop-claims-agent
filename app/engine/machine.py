from collections.abc import Callable
from datetime import date

from app.config import Settings
from app.data.repos import Repos
from app.engine.briefs import HandlerResult, merge_briefs
from app.engine.context import resolve_pending
from app.engine.memory import flagged_identity_check, merge_analysis
from app.engine.phases import HANDLERS
from app.engine.policies import pass1, pass2
from app.engine.state import PendingAsk, Session, Turn
from app.llm.schemas import ReplyBrief, TurnAnalysis

GREETING = (
    "Hello, I'm an automated assistant for claims support. I can help with questions about your claims "
    "once I've verified your identity. To start, please tell me your full name and policy number."
)
MAX_CHAIN = 4


class Engine:
    def __init__(self, repos: Repos, settings: Settings, today: Callable[[], date] = date.today):
        self.repos = repos
        self.settings = settings
        self.today = today

    def greeting(self, session: Session) -> str:
        session.pending_ask = PendingAsk.IDENTITY_FIELDS
        session.last_brief = ReplyBrief(phase="VERIFY_ID", goal="Greet and ask for name and policy number.",
                                        ask="Could you tell me your full name and policy number?")
        session.transcript.append(Turn(role="assistant", text=GREETING))
        session.log("greeting")
        return GREETING

    def handle_turn(self, session: Session, analysis: TurnAnalysis, user_text: str) -> ReplyBrief:
        session.turn += 1
        session.transcript.append(Turn(role="user", text=user_text))
        ctx = resolve_pending(session, analysis, user_text)
        if analysis.injection_suspected:  # an injection-flagged turn never changes memory
            flagged_identity_check(session, analysis)  # but a name in it can still raise the question
        else:
            changed = merge_analysis(session, analysis)
            if session.fence_turn == session.turn:  # a verification reset happened on this turn
                # the context was read against the earlier party's pending question; read it again now
                ctx = resolve_pending(session, analysis, user_text)
            ctx.changed_slots = changed
        pass1(session, ctx, self.settings)
        if ctx.policy_brief is not None:
            brief = ctx.policy_brief
        else:
            results: list[HandlerResult] = []
            for _ in range(MAX_CHAIN):
                handler = HANDLERS.get(session.phase)
                if handler is None:
                    break
                result = handler(session, ctx, self.repos, self.settings, self.today())
                results.append(result)
                if not (result.advanced and not result.needs_input):
                    break
            brief = (merge_briefs(results) if results
                     else ReplyBrief(phase=session.phase.value, goal="Continue."))
        brief = pass2(session, ctx, brief)
        session.last_brief = brief
        return brief
