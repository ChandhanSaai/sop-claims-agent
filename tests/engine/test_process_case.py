from datetime import date

from app.engine.context import HUMAN_ASK
from app.engine.machine import Engine
from app.engine.phases import process_case, resolve_intent
from app.engine.state import PendingAsk, Phase, Session
from app.llm.schemas import TurnAnalysis
from tests.engine.helpers import turn, verified_session


def in_case(repos, settings, case_id="CL-2048"):
    s = verified_session(repos)
    turn(s, repos, settings, resolve_intent.handle, case_hints={"case_id": case_id}, intent="denial_question")
    assert s.phase == Phase.PROCESS_CASE
    return s


def test_first_answer_gives_status_denial_documents_and_passed_deadline(repos, settings):
    s = in_case(repos, settings)
    r = turn(s, repos, settings, process_case.handle, intent="denial_question")
    f = r.brief.allowed_facts
    assert f["claim_id"] == "CL-2048" and f["claim_status"] == "denied"
    assert ("pathology report" in f["denial_reason"]
            and f["documents_needed"] == "pathology report, office note")
    assert f["appeal_deadline"] == "March 18, 2026" and f["deadline_passed"] == "yes"
    assert f["allowed_max_amount"] == "$1450.00"
    assert any("has passed" in m for m in r.brief.must_say)
    assert r.brief.ask == process_case.ANYTHING_ELSE_ASK and s.pending_ask == PendingAsk.ANYTHING_ELSE
    assert s.events[-1].type == "answered"


def test_document_submission_adds_guidance_facts(repos, settings):
    s = in_case(repos, settings)
    r = turn(s, repos, settings, process_case.handle, intent="document_submission",
             followup_topic="submission_method")
    f = r.brief.allowed_facts
    assert "member portal" in f["topic_submission_method"]
    assert "patient name" in f["guidance_original_pathology_report"]
    assert "visit date" in f["guidance_treating_provider_office_note"]
    assert "medical claims" in f["case_type_guidance"]


def test_processing_time_topic_and_fallback_for_claim_without_documents(repos, settings):
    s = in_case(repos, settings)
    r = turn(s, repos, settings, process_case.handle, intent="next_steps",
             followup_topic="processing_time_after_submission")
    assert "less than a week" in r.brief.allowed_facts["topic_processing_time_after_submission"]
    auto = in_case(repos, settings, "CL-2102")
    r2 = turn(auto, repos, settings, process_case.handle, intent="next_steps",
              followup_topic="processing_time_after_submission")
    assert "topic_processing_time_after_submission" not in r2.brief.allowed_facts
    assert "fallback_guidance" in r2.brief.allowed_facts and "documents_needed" not in r2.brief.allowed_facts


def test_cannot_get_document_offers_alternatives_and_human(repos, settings):
    s = in_case(repos, settings)
    turn(s, repos, settings, process_case.handle, intent="denial_question")
    r = turn(s, repos, settings, process_case.handle,
             user_text="I can't get the pathology report, the lab closed",
             intent="next_steps", followup_topic="missing_required_material_alternatives",
             question="I can't get the pathology report because the lab closed")
    f = r.brief.allowed_facts
    assert "replacement copy" in f["alternative_original_pathology_report"]
    assert "alternative_treating_provider_office_note" not in f
    assert "human claims representative" in f["human_review"]
    assert r.brief.offer_human and r.brief.ask == HUMAN_ASK and s.pending_ask == PendingAsk.HUMAN_OFFER


def test_late_appeal_question_routes_to_human(repos, settings):
    s = in_case(repos, settings)
    r = turn(s, repos, settings, process_case.handle, intent="next_steps",
             question="Can I still appeal this?")
    assert r.brief.offer_human and any("late appeal" in m for m in r.brief.must_say)


def test_closing_enters_post_process_in_same_turn(repos, settings):
    s = in_case(repos, settings)
    turn(s, repos, settings, process_case.handle, intent="denial_question")
    r = turn(s, repos, settings, process_case.handle, requests={"confirmation": "no", "closing": True})
    assert s.phase == Phase.POST_PROCESS and r.advanced and not r.needs_input


def test_switch_claim_clears_stale_hints_and_returns_to_resolve(repos, settings):
    s = verified_session(repos)
    turn(s, repos, settings, resolve_intent.handle,
         case_hints={"case_type": "healthcare", "status": "denied", "month": 1})
    r = turn(s, repos, settings, process_case.handle, case_hints={"case_type": "auto"},
             requests={"switch_claim": True})
    assert s.phase == Phase.RESOLVE_INTENT and r.advanced and s.case.selected_case_id is None
    assert s.memory.value("status_hint") is None and s.memory.value("month") is None
    r2 = turn(s, repos, settings, resolve_intent.handle)
    assert s.case.selected_case_id == "CL-2102" and r2.advanced


def test_full_chain_margaret_one_turn(repos, settings):
    eng = Engine(repos, settings, today=lambda: date(2026, 10, 7))
    s = Session.new()
    eng.greeting(s)
    analysis = TurnAnalysis.model_validate({
        "identity": {"full_name": "Margaret Chen", "policy_number": "POL-9921", "dob": "1985-03-15",
                     "id_last4": "4472"},
        "caller_role": "policyholder",
        "case_hints": {"case_type": "healthcare", "status": "denied", "month": 1},
        "intent": "denial_question",
    })
    brief = eng.handle_turn(s, analysis, "I'm the policyholder ...")
    assert s.phase == Phase.PROCESS_CASE and s.pending_ask == PendingAsk.ANYTHING_ELSE
    assert brief.must_say[0] == "Identity verification is complete."
    assert brief.must_say[1].startswith("The claim you mentioned is CL-2048")
    assert brief.allowed_facts["claim_id"] == "CL-2048" and "denial_reason" in brief.allowed_facts
    assert not any("Do not confirm or deny" in m for m in brief.must_not)


def test_closed_status_question_is_not_a_missing_document(repos, settings):
    s = in_case(repos, settings)
    r = turn(s, repos, settings, process_case.handle, intent="status_inquiry",
             question="Is this claim closed now?")
    assert not any(k.startswith("alternative_") for k in r.brief.allowed_facts)
    assert not r.brief.offer_human and s.pending_ask == PendingAsk.ANYTHING_ELSE


def test_next_steps_on_claim_without_documents_skips_submission(repos, settings):
    s = in_case(repos, settings, "CL-2102")
    r = turn(s, repos, settings, process_case.handle, intent="next_steps")
    assert "submission_guidance" not in r.brief.allowed_facts
    assert not any(m.startswith("Explain what to send") for m in r.brief.must_say)
    assert r.brief.ask == process_case.ANYTHING_ELSE_ASK and s.pending_ask == PendingAsk.ANYTHING_ELSE


def test_switch_drops_previous_claims_intent(repos, settings):
    s = in_case(repos, settings)
    turn(s, repos, settings, process_case.handle, case_hints={"case_type": "auto"},
         requests={"switch_claim": True})
    turn(s, repos, settings, resolve_intent.handle)
    assert s.case.selected_case_id == "CL-2102" and s.case.intent == "general_claim_question"


def test_no_to_anything_else_while_switching_answers_new_claim(repos, settings):
    s = in_case(repos, settings)
    turn(s, repos, settings, process_case.handle, intent="denial_question")
    eng = Engine(repos, settings, today=lambda: date(2026, 10, 7))
    analysis = TurnAnalysis.model_validate({"requests": {"confirmation": "no"},
                                            "case_hints": {"case_type": "auto"}})
    brief = eng.handle_turn(s, analysis, "No, but what about my auto claim?")
    assert s.phase == Phase.PROCESS_CASE and brief.allowed_facts["claim_id"] == "CL-2102"
    assert brief.ask == process_case.ANYTHING_ELSE_ASK and s.pending_ask == PendingAsk.ANYTHING_ELSE


def test_explicit_switch_answers_new_claim_in_same_turn(repos, settings):
    s = in_case(repos, settings)
    eng = Engine(repos, settings, today=lambda: date(2026, 10, 7))
    analysis = TurnAnalysis.model_validate({"case_hints": {"case_type": "auto"},
                                            "requests": {"switch_claim": True}})
    brief = eng.handle_turn(s, analysis, "Actually, can we talk about my auto claim?")
    assert s.phase == Phase.PROCESS_CASE and s.case.selected_case_id == "CL-2102"
    selected = [e.data["case_id"] for e in s.events if e.turn == s.turn and e.type == "claim_selected"]
    assert selected == ["CL-2102"]
    assert brief.ask
    fact = "The claim you mentioned is CL-2102, auto claim opened February 28, 2026, status open."
    assert brief.must_say.count(fact) == 1


def test_submission_answer_says_no_chat_upload_and_offers_the_checklist(repos, settings):
    s = in_case(repos, settings)
    r = turn(s, repos, settings, process_case.handle, intent="document_submission",
             followup_topic="submission_method")
    assert process_case.NO_CHAT_UPLOAD in r.brief.must_say
    assert process_case.SUBMISSION_SHORT in r.brief.must_say
    assert "submission_guidance" in r.brief.allowed_facts
    assert any(k.startswith("guidance_") for k in r.brief.allowed_facts)  # the checklist stays available
    r2 = turn(s, repos, settings, process_case.handle, intent="document_submission",
              followup_topic="file_format_requirements")
    assert process_case.SUBMISSION_DETAIL in r2.brief.must_say
    assert process_case.NO_CHAT_UPLOAD in r2.brief.must_say
