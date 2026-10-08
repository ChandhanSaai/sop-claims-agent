from app.engine.phases import post_process, process_case, resolve_intent
from app.engine.state import PendingAsk, Phase
from tests.engine.helpers import turn, verified_session


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
    turn(s2, repos, settings, post_process.handle, requests={"confirmation": "no"})
    assert repos.outbox.list() == [] and s2.pending_ask == PendingAsk.NONE
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
