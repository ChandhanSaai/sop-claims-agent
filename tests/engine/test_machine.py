from datetime import date

from app.engine.machine import Engine
from app.engine.phases import HANDLERS
from app.engine.state import PendingAsk, Phase, Session
from app.llm.schemas import TurnAnalysis


def test_greeting_discloses_automation_and_sets_pending(repos, settings):
    eng = Engine(repos, settings, today=lambda: date(2026, 10, 7))
    s = Session.new()
    text = eng.greeting(s)
    assert "automated assistant" in text
    assert s.pending_ask == PendingAsk.IDENTITY_FIELDS
    assert s.transcript[0].role == "assistant"


def test_handle_turn_runs_verify_and_stops_when_next_phase_has_no_handler(repos, settings, monkeypatch):
    # RESOLVE_INTENT is registered now; keep this test about chain termination.
    monkeypatch.delitem(HANDLERS, Phase.RESOLVE_INTENT)
    eng = Engine(repos, settings, today=lambda: date(2026, 10, 7))
    s = Session.new()
    eng.greeting(s)
    analysis = TurnAnalysis.model_validate({
        "identity": {"full_name": "Margaret Chen", "dob": "1985-03-15", "id_last4": "4472"},
        "case_hints": {"case_type": "healthcare", "status": "denied", "month": 1},
        "intent": "denial_question",
    })
    brief = eng.handle_turn(s, analysis, "My name is Margaret Chen ...")
    assert s.turn == 1 and s.phase == Phase.RESOLVE_INTENT
    assert s.transcript[-1].role == "user"
    assert brief.must_say[0] == "Identity verification is complete."
    assert s.last_brief is brief


def test_frustration_streak_adds_human_offer(repos, settings):
    eng = Engine(repos, settings)
    s = Session.new()
    angry = {"identity": {"full_name": "Margaret Chen"},
             "affect": {"frustration": 3, "anger": 2, "refusal": True}}
    b1 = eng.handle_turn(s, TurnAnalysis.model_validate(angry), "ridiculous")
    assert b1.tone == "de_escalate" and b1.acknowledge and not b1.offer_human
    b2 = eng.handle_turn(s, TurnAnalysis.model_validate(angry), "still ridiculous")
    assert b2.offer_human and s.pending_ask == PendingAsk.HUMAN_OFFER
