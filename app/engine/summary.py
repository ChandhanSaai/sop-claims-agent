from datetime import date

from app.data.normalize import fmt_date
from app.data.repos import Repos
from app.engine.state import Session


def build_summary(session: Session, repos: Repos, today: date) -> str:
    """Built from the structured event log and the system of record; never from the transcript."""
    claim = repos.claims.get(session.case.selected_case_id) if session.case.selected_case_id else None
    answered = [e for e in session.events if e.type == "answered"]
    facts_used: set[str] = set()
    for e in answered:
        facts_used.update(e.data.get("facts", []))
    lines = [f"Summary of your claims support conversation ({fmt_date(today)})", ""]
    if claim:
        lines += [f"Claim: {claim.case_id} ({claim.case_type}) - status: {claim.status}", "",
                  "What we discussed:"]
        if claim.denial_reason and "denial_reason" in facts_used:
            lines.append(f"- Why the claim was denied: {claim.denial_reason}.")
        if claim.documents_needed and "documents_needed" in facts_used:
            lines.append(f"- Documents needed: {', '.join(claim.documents_needed)}.")
        if "topic_submission_method" in facts_used or "submission_guidance" in facts_used:
            lines.append("- How to submit: use the member portal or claim upload link; fax or mail can be "
                         "arranged if online upload is not available.")
        if "topic_processing_time_after_submission" in facts_used:
            lines.append(f"- Processing time after submission: {repos.guideline.processing_time()}; "
                         "the review restarts once the files are received.")
        if any(k.startswith("alternative_") for k in facts_used):
            lines.append("- If a document is unavailable: request a replacement copy from the provider, lab "
                         "or clinic; a representative can review manual options.")
        if len(lines) and lines[-1] == "What we discussed:":
            lines.append(f"- The current status of claim {claim.case_id}.")
        lines += ["", "Next steps:"]
        if claim.documents_needed:
            lines.append(f"- Submit {', '.join(claim.documents_needed)} for claim {claim.case_id}.")
        if claim.appeal_deadline:
            passed = claim.appeal_deadline < today
            lines.append(f"- Appeal deadline: {fmt_date(claim.appeal_deadline)}"
                         + (" (this date has passed; a representative will review options)."
                            if passed else "."))
    if session.escalation.requested:
        lines.append(f"- A representative will follow up. Reference: {session.escalation.reference}.")
    lines += ["", "This summary contains no identification details. If anything looks wrong, reply to this "
              "email or call support."]
    return "\n".join(lines)
