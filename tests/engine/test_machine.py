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


def test_one_turn_verify_resolve_answer_says_each_transition_fact_once(repos, settings):
    eng = Engine(repos, settings, today=lambda: date(2026, 10, 7))
    s = Session.new()
    eng.greeting(s)
    brief = eng.handle_turn(s, TurnAnalysis.model_validate({
        "identity": {"full_name": "Margaret Chen", "dob": "1985-03-15", "id_last4": "4472"},
        "case_hints": {"case_type": "healthcare", "status": "denied", "month": 1},
        "intent": "denial_question",
    }), "Margaret Chen, 1985-03-15, 4472, my denied January healthcare claim")
    assert s.phase == Phase.PROCESS_CASE and brief.phase == "PROCESS_CASE"
    fact = "The claim you mentioned is CL-2048, healthcare claim opened January 12, 2026, status denied."
    assert brief.must_say[:2] == ["Identity verification is complete.", fact]
    assert len(brief.must_say) == len(set(brief.must_say))  # the lead facts are not repeated by the answer
    assert brief.allowed_facts["claim_id"] == "CL-2048" and brief.ask


def test_a_plain_yes_or_no_to_the_identity_question_is_read_by_code():
    from app.engine.context import resolve_pending
    from app.engine.state import IdentityQuestion, PendingAsk, Session
    from app.llm.schemas import TurnAnalysis

    s = Session.new()
    s.pending_identity = IdentityQuestion(candidate="Ma Tian", who="Margaret Chen")
    s.pending_ask = PendingAsk.IDENTITY_CONFIRM
    for text, expected in (("No. Ma Tian, born 1964-09-10, last four 6688.", "no"), ("Nope", "no"),
                           ("no, this is Ma Tian", "no"), ("Yes.", "yes"), ("yes it's me", "yes"),
                           ("It's me, Margaret", "yes"), ("No wonder it took so long", "unspecified"),
                           ("Yesterday I called", "unspecified"), ("Notably, no", "unspecified")):
        a = TurnAnalysis.empty()
        resolve_pending(s, a, text)
        assert a.requests.confirmation == expected, text
    a = TurnAnalysis.model_validate({"requests": {"confirmation": "yes"}})
    resolve_pending(s, a, "No.")  # the Reader's own reading stands
    assert a.requests.confirmation == "yes"
