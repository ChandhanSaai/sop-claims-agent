from datetime import date

from app.engine.state import Escalation
from app.engine.summary import build_summary
from tests.engine.helpers import verified_session


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
