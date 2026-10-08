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
