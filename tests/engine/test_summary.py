from datetime import date

from app.engine.machine import Engine
from app.engine.state import HINT_SLOTS, Escalation, PendingAsk, Phase, Session
from app.engine.summary import build_summary
from app.llm.schemas import TurnAnalysis
from tests.engine.helpers import TODAY, verified_session


def A(**kw):
    return TurnAnalysis.model_validate(kw)


def test_summary_is_built_from_events_and_data_without_identifiers(repos, settings):
    s = verified_session(repos)
    s.case.selected_case_id = "CL-2048"
    s.log("answered", intent="denial_question", topic=None,
          facts=["claim_status", "denial_reason", "documents_needed", "appeal_deadline"])
    s.log("answered", intent="document_submission", topic="submission_method",
          facts=["topic_submission_method", "submission_guidance"])
    s.log("answered", intent="next_steps", topic="processing_time_after_submission",
          facts=["topic_processing_time_after_submission"])
    body = build_summary(s, repos, date(2026, 10, 7))
    assert "CL-2048" in body and "status: denied" in body
    assert "pathology report" in body and "office note" in body
    assert "member portal" in body and "usually less than a week" in body
    assert "March 18, 2026" in body and "has passed" in body
    for leak in ("1985", "4472", "6505212836", "margaret@email.com", "POL-9921"):
        assert leak not in body


def test_summary_mentions_escalation_reference(repos, settings):
    s = verified_session(repos)
    s.case.selected_case_id = "CL-2048"
    s.escalation = Escalation(requested=True, reference="ESC-TEST01", reason="test")
    assert "ESC-TEST01" in build_summary(s, repos, date(2026, 10, 7))


def test_summary_has_one_section_per_claim_in_the_order_discussed(repos, settings):
    s = verified_session(repos)
    s.log("claim_selected", case_id="CL-2048", intent="denial_question")
    s.log("answered", intent="denial_question", topic=None,
          facts=["claim_status", "denial_reason", "documents_needed", "appeal_deadline"])
    s.log("answered", intent="document_submission", topic="submission_method",
          facts=["topic_submission_method", "submission_guidance"])
    s.log("claim_selected", case_id="CL-2102", intent="status_inquiry")
    s.log("answered", intent="status_inquiry", topic=None, facts=["claim_status", "claim_summary"])
    s.case.selected_case_id = "CL-2102"
    s.escalation = Escalation(requested=True, reference="ESC-TEST01", reason="test")
    first, second = build_summary(s, repos, date(2026, 10, 7)).split("Claim: CL-2102")
    assert "Claim: CL-2048 (healthcare) - status: denied" in first
    assert "pathology report" in first and "member portal" in first
    assert "- Submit pathology report, office note for claim CL-2048." in first
    assert "March 18, 2026 (this date has passed; a representative can review options)." in first
    assert "(auto) - status: open" in second and "- The current status of claim CL-2102." in second
    assert "member portal" not in second and "Next steps:" not in second
    assert "ESC-TEST01" not in first and second.count("ESC-TEST01") == 1
    assert second.rstrip().endswith("reply to this email or call support.")


def test_reverification_as_another_party_fences_off_the_earlier_claim(repos, settings):
    eng = Engine(repos, settings, today=lambda: TODAY)
    s = Session.new()
    eng.greeting(s)
    eng.handle_turn(s, A(identity={"full_name": "Margaret Chen", "dob": "1985-03-15", "id_last4": "4472"},
                         case_hints={"case_type": "healthcare", "status": "denied", "month": 1},
                         intent="denial_question"),
                    "Margaret Chen, 1985-03-15, 4472, my denied January healthcare claim")
    assert s.case.selected_case_id == "CL-2048" and any(e.type == "answered" for e in s.events)
    b = eng.handle_turn(s, A(corrections=[{"slot": "full_name", "new_value": "Ma Tian"},
                                          {"slot": "dob", "new_value": "1964-09-10"},
                                          {"slot": "id_last4", "new_value": "6688"}]),
                        "Sorry, this is actually Ma Tian, born 1964-09-10, last four 6688.")
    assert (s.verification.status, s.verification.party_id) == ("verified", "P12")
    assert s.phase == Phase.PROCESS_CASE and s.case.selected_case_id == "CL-3001"  # his only claim
    assert not any(n in s.memory.slots for n in HINT_SLOTS)  # her hints did not filter his claims
    assert "CL-2048" not in " ".join(b.must_say) and "pathology" not in " ".join(b.allowed_facts.values())
    body = build_summary(s, repos, TODAY)
    assert "CL-3001" in body and "CL-2048" not in body and "pathology" not in body and "Margaret" not in body
    eng.handle_turn(s, A(requests={"closing": True}), "That's all.")
    assert s.pending_ask == PendingAsk.EMAIL_OFFER
    draft = eng.handle_turn(s, A(requests={"confirmation": "yes"}), "Yes please.")
    assert "CL-3001" in draft.verbatim and "CL-2048" not in draft.verbatim
    eng.handle_turn(s, A(requests={"confirmation": "yes"}), "Yes, send it.")
    sent = repos.outbox.list()
    assert len(sent) == 1 and sent[0].to == "matian@example.com"
    assert "CL-3001" in sent[0].body and "CL-2048" not in sent[0].body and "Margaret" not in sent[0].body


def test_the_fence_keeps_the_correction_message_and_resets_the_email_offer(repos, settings):
    from app.engine.machine import Engine
    from app.engine.state import Session
    from app.llm.schemas import TurnAnalysis

    eng = Engine(repos, settings)
    s = Session.new()
    eng.greeting(s)
    first = {"identity": {"full_name": "Margaret Chen", "dob": "1985-03-15", "id_last4": "4472"},
             "intent": "denial_question",
             "case_hints": {"status": "denied", "case_type": "healthcare", "month": 1}}
    eng.handle_turn(s, TurnAnalysis.model_validate(first), "Margaret Chen, 1985-03-15, 4472, my denied claim")
    s.counters.email_offered = True
    tian = next(r for r in repos.policyholders._records if r.name == "Ma Tian")
    corrections = [{"slot": "full_name", "new_value": tian.name},
                   {"slot": "dob", "new_value": tian.dob.isoformat()},
                   {"slot": "id_last4", "new_value": tian.id_last4}]
    text = "sorry, this is actually Ma Tian"
    eng.handle_turn(s, TurnAnalysis.model_validate({"corrections": corrections}), text)
    assert s.verification.party_id == tian.party_id
    assert s.transcript[s.transcript_fence].text == text  # the correction stays in the Writer window
    assert s.counters.email_offered is False
