import pytest

from app.engine.machine import Engine
from app.engine.phases import post_process, process_case, resolve_intent
from app.engine.state import PendingAsk, Phase
from app.llm.schemas import TurnAnalysis
from tests.engine.helpers import TODAY, turn, verified_session


def closed_case(repos, settings):
    s = verified_session(repos)
    turn(s, repos, settings, resolve_intent.handle, case_hints={"case_id": "CL-2048"},
         intent="denial_question")
    turn(s, repos, settings, process_case.handle, intent="denial_question")
    turn(s, repos, settings, process_case.handle, requests={"confirmation": "no", "closing": True})
    assert s.phase == Phase.POST_PROCESS
    return s


def test_offer_once_with_masked_address(repos, settings):
    s = closed_case(repos, settings)
    r = turn(s, repos, settings, post_process.handle)
    assert s.counters.email_offered and s.pending_ask == PendingAsk.EMAIL_OFFER
    assert r.brief.allowed_facts["email_on_file_masked"] == "m*******@email.com"
    assert r.brief.ask


def test_yes_shows_draft_then_confirm_sends(repos, settings):
    s = closed_case(repos, settings)
    turn(s, repos, settings, post_process.handle)
    r = turn(s, repos, settings, post_process.handle,
             requests={"confirmation": "yes", "email_summary": "yes"})
    assert s.pending_ask == PendingAsk.EMAIL_CONFIRM and "CL-2048" in r.brief.verbatim
    assert "1985" not in r.brief.verbatim and "4472" not in r.brief.verbatim
    r2 = turn(s, repos, settings, post_process.handle, requests={"confirmation": "yes"})
    assert s.pending_ask == PendingAsk.NONE and r2.brief.allowed_facts["email_reference"] == "EML-0001"
    assert [e.data for e in s.events if e.type == "email_sent"] == [
        {"email_id": "EML-0001", "to_masked": "m*******@email.com"}]
    assert repos.outbox.list()[0].to == "margaret@email.com"
    assert "1985" not in repos.outbox.list()[0].body


def test_no_at_offer_and_no_at_confirm_send_nothing(repos, settings):
    s = closed_case(repos, settings)
    turn(s, repos, settings, post_process.handle)
    r = turn(s, repos, settings, post_process.handle, requests={"confirmation": "no", "email_summary": "no"})
    assert (s.pending_ask == PendingAsk.NONE and repos.outbox.list() == []
            and "Nothing will be sent." in r.brief.must_say)
    s2 = closed_case(repos, settings)
    turn(s2, repos, settings, post_process.handle)
    turn(s2, repos, settings, post_process.handle, requests={"confirmation": "yes"})
    r2 = turn(s2, repos, settings, post_process.handle, requests={"confirmation": "no"})
    assert repos.outbox.list() == [] and s2.pending_ask == PendingAsk.NONE
    assert "Nothing will be sent." in r2.brief.must_say and s2.pending_draft is None
    r3 = turn(s2, repos, settings, post_process.handle, requests={"closing": True})
    assert (not r3.brief.offer_human and s2.counters.email_offered
            and "email" not in " ".join(r3.brief.must_say).lower())


def test_new_question_after_goodbye_routes_back(repos, settings):
    s = closed_case(repos, settings)
    turn(s, repos, settings, post_process.handle)
    turn(s, repos, settings, post_process.handle, requests={"confirmation": "no"})
    r = turn(s, repos, settings, post_process.handle, intent="status_inquiry",
             question="wait, what's the status again?")
    assert s.phase == Phase.RESOLVE_INTENT and r.advanced and not r.needs_input


def test_unclear_answer_at_confirm_is_a_no(repos, settings):
    s = closed_case(repos, settings)
    turn(s, repos, settings, post_process.handle)
    turn(s, repos, settings, post_process.handle, requests={"confirmation": "yes"})
    r = turn(s, repos, settings, post_process.handle, "hmm")
    assert r.brief.must_say[0] == "Nothing will be sent." and not r.advanced
    assert s.pending_draft is None and s.pending_ask == PendingAsk.NONE and repos.outbox.list() == []


def test_no_with_a_question_routes_back_after_nothing_will_be_sent(repos, settings):
    s = closed_case(repos, settings)
    turn(s, repos, settings, post_process.handle)
    r = turn(s, repos, settings, post_process.handle, requests={"confirmation": "no"},
             intent="status_inquiry", question="no thanks, but what's the status again?")
    assert s.phase == Phase.RESOLVE_INTENT and r.advanced and not r.needs_input
    assert r.transition_fact == "Nothing will be sent." and repos.outbox.list() == []


@pytest.mark.parametrize("pending, requests", [
    (PendingAsk.NONE, {"closing": True}),
    (PendingAsk.ANYTHING_ELSE, {"confirmation": "no"}),  # anything_else_no
])
def test_second_close_with_a_new_hint_says_goodbye(repos, settings, pending, requests):
    s = closed_case(repos, settings)
    turn(s, repos, settings, post_process.handle)
    turn(s, repos, settings, post_process.handle, requests={"confirmation": "no"})
    s.pending_ask = pending
    r = turn(s, repos, settings, post_process.handle, requests=requests, case_hints={"status": "closed"})
    assert s.phase == Phase.POST_PROCESS and not r.advanced
    assert r.brief.must_say == [post_process.GOODBYE]


def say(eng, s, text, **analysis_fields):
    return eng.handle_turn(s, TurnAnalysis.model_validate(analysis_fields), text)


def engine_with_claim(repos, settings):
    eng = Engine(repos, settings, today=lambda: TODAY)
    s = verified_session(repos)
    say(eng, s, "Why was CL-2048 denied?", case_hints={"case_id": "CL-2048"}, intent="denial_question")
    assert s.case.selected_case_id == "CL-2048" and s.phase == Phase.PROCESS_CASE
    return eng, s


def test_engine_closing_offer_yes_confirm_sends_to_the_address_on_file(repos, settings):
    eng, s = engine_with_claim(repos, settings)
    offer = say(eng, s, "No, that's all.", requests={"confirmation": "no", "closing": True})
    assert s.pending_ask == PendingAsk.EMAIL_OFFER and offer.allowed_facts["email_on_file_masked"]
    draft = say(eng, s, "Yes please.", requests={"confirmation": "yes"})
    assert s.pending_ask == PendingAsk.EMAIL_CONFIRM and "CL-2048" in draft.verbatim
    final = say(eng, s, "Yes, send it.", requests={"confirmation": "yes"})
    sent = repos.outbox.list()
    assert len(sent) == 1 and sent[0].to == "margaret@email.com"
    assert final.allowed_facts["email_reference"] == sent[0].id


def test_engine_unclear_answer_at_offer_with_a_question_says_nothing_will_be_sent_first(repos, settings):
    eng, s = engine_with_claim(repos, settings)
    say(eng, s, "No, that's all.", requests={"confirmation": "no", "closing": True})
    b = say(eng, s, "What's the status again?", intent="status_inquiry", question="What's the status again?")
    assert b.must_say[0] == "Nothing will be sent." and s.phase == Phase.PROCESS_CASE
    assert s.pending_draft is None and repos.outbox.list() == []
