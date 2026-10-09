from datetime import date

from app.data.normalize import fmt_date
from app.data.repos import Repos
from app.engine.state import Session


def build_summary(session: Session, repos: Repos, today: date) -> str:
    """Built from the structured event log and the system of record; never from the transcript.
    One section per claim, in the order the claims were first discussed."""
    facts_by_claim: dict[str, set[str]] = {}
    current = None
    for e in session.events:
        if e.type == "claim_selected":
            current = e.data["case_id"]
            facts_by_claim.setdefault(current, set())
        # an answer with no selection event before it belongs to the selected claim
        elif e.type == "answered" and (cid := current or session.case.selected_case_id):
            facts_by_claim.setdefault(cid, set()).update(e.data.get("facts", []))
    lines = [f"Summary of your claims support conversation ({fmt_date(today)})"]
    for case_id, facts_used in facts_by_claim.items():
        claim = repos.claims.get(case_id)
        lines += ["", f"Claim: {claim.case_id} ({claim.case_type}) - status: {claim.status}", "",
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
        if lines[-1] == "What we discussed:":
            lines.append(f"- The current status of claim {claim.case_id}.")
        steps = []
        if claim.documents_needed:
            steps.append(f"- Submit {', '.join(claim.documents_needed)} for claim {claim.case_id}.")
        if claim.appeal_deadline:
            passed = claim.appeal_deadline < today
            steps.append(f"- Appeal deadline: {fmt_date(claim.appeal_deadline)}"
                         + (" (this date has passed; a representative can review options)."
                            if passed else "."))
        if steps:
            lines += ["", "Next steps:", *steps]
    if session.escalation.requested:
        lines += ["", f"A representative will follow up. Reference: {session.escalation.reference}."]
    if session.verification.role == "representative":
        c = session.consent
        lines += ["", f"Discussed with your authorized representative {c.representative_name} "
                      f"(consent reference {c.consent_id})."]
    lines += ["", "This summary contains no identification details. If anything looks wrong, reply to this "
              "email or call support."]
    return "\n".join(lines)
