from app.engine.phases import resolve_intent
from app.engine.state import PendingAsk, Phase
from tests.engine.helpers import turn, verified_session


def test_unique_hints_select_and_advance(repos, settings):
    s = verified_session(repos)
    r = turn(s, repos, settings, resolve_intent.handle,
             case_hints={"case_type": "healthcare", "status": "denied", "month": 1}, intent="denial_question")
    assert s.phase == Phase.PROCESS_CASE and s.case.selected_case_id == "CL-2048"
    assert s.case.intent == "denial_question"
    assert r.advanced and not r.needs_input
    assert "CL-2048" in r.transition_fact and r.transition_facts["claim_opened"] == "January 12, 2026"


def test_decoy_asks_then_ordinal_selects(repos, settings):
    s = verified_session(repos)
    r = turn(s, repos, settings, resolve_intent.handle, case_hints={"case_type": "healthcare", "month": 1})
    assert s.phase == Phase.RESOLVE_INTENT and s.pending_ask == PendingAsk.DISAMBIGUATION
    assert set(s.case.candidates) == {"CL-2048", "CL-2011"}
    assert "CL-2048" in r.brief.allowed_facts["option_1"] + r.brief.allowed_facts["option_2"]
    assert r.brief.ask
    first = s.case.candidates[0]
    r2 = turn(s, repos, settings, resolve_intent.handle, user_text="the first one")
    assert s.phase == Phase.PROCESS_CASE and s.case.selected_case_id == first and r2.advanced


def test_selection_by_case_id_and_by_new_hint(repos, settings):
    s = verified_session(repos)
    turn(s, repos, settings, resolve_intent.handle, case_hints={"case_type": "healthcare", "month": 1})
    turn(s, repos, settings, resolve_intent.handle, user_text="CL-2011", case_hints={"case_id": "cl-2011"})
    assert s.case.selected_case_id == "CL-2011"
    s2 = verified_session(repos)
    turn(s2, repos, settings, resolve_intent.handle, case_hints={"case_type": "healthcare", "month": 1})
    turn(s2, repos, settings, resolve_intent.handle, user_text="the denied one",
         case_hints={"status": "denied"})
    assert s2.case.selected_case_id == "CL-2048"


def test_no_hints_lists_all_claims(repos, settings):
    s = verified_session(repos)
    r = turn(s, repos, settings, resolve_intent.handle)
    assert len(r.brief.allowed_facts) == 4 and s.pending_ask == PendingAsk.DISAMBIGUATION
    assert "Here are the claims on file." in r.brief.must_say


def test_no_match_says_so_and_lists(repos, settings):
    s = verified_session(repos)
    r = turn(s, repos, settings, resolve_intent.handle,
             case_hints={"case_type": "dental", "status": "denied"})
    assert r.brief.must_say[0].startswith("I don't see a claim matching")
    assert len(s.case.candidates) == 4


def test_returning_with_same_claim_reselects_immediately(repos, settings):
    s = verified_session(repos)
    turn(s, repos, settings, resolve_intent.handle, case_hints={"case_id": "CL-2048"})
    s.phase = Phase.RESOLVE_INTENT
    r = turn(s, repos, settings, resolve_intent.handle, intent="status_inquiry")
    assert r.advanced and s.case.selected_case_id == "CL-2048"


def test_returning_with_consistent_new_hint_keeps_claim(repos, settings):
    s = verified_session(repos)
    turn(s, repos, settings, resolve_intent.handle,
         case_hints={"case_type": "healthcare", "status": "denied", "month": 1})
    s.phase = Phase.RESOLVE_INTENT
    r = turn(s, repos, settings, resolve_intent.handle, case_hints={"year": 2026})
    assert r.advanced and s.case.selected_case_id == "CL-2048"
    s.phase = Phase.RESOLVE_INTENT
    turn(s, repos, settings, resolve_intent.handle, case_hints={"case_type": "auto"})
    assert s.case.selected_case_id == "CL-2102"
