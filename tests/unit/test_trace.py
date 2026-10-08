import json

from app.engine.state import Session, Verification
from app.llm.schemas import ReplyBrief, TurnAnalysis
from app.observability.trace import TraceRecord, TraceWriter, disclosure_event


def test_trace_record_masks_identity_and_writes_redacted_jsonl(tmp_path):
    analysis = TurnAnalysis.model_validate({"identity": {"dob": "1985-03-15", "full_name": "Margaret Chen"},
                                            "intent": "denial_question"})
    rec = TraceRecord.build(
        session_id="abc", turn=1, phase_before="VERIFY_ID", phase_after="PROCESS_CASE",
        pending_ask="anything_else", analysis=analysis, changed_slots=["dob"],
        brief=ReplyBrief(phase="PROCESS_CASE", goal="g"), guard={"ok": True, "violations": []},
        reader_model="r", writer_model="w", latency_ms=12, reply_text="Hello margaret@email.com")
    assert (rec.analysis["identity"]["dob"] == "******"
            and rec.analysis["identity"]["full_name"] == "Margaret Chen")
    assert rec.analysis["intent"] == "denial_question"
    w = TraceWriter(tmp_path / "traces")
    w.write(rec)
    line = (tmp_path / "traces" / "abc.jsonl").read_text().strip()
    data = json.loads(line)
    assert data["turn"] == 1 and "margaret@email.com" not in line and "1985-03-15" not in line


def test_disclosure_event_only_when_verified_with_facts():
    s = Session.new()
    disclosure_event(s, ReplyBrief(phase="p", goal="g", allowed_facts={"claim_id": "CL-2048"}))
    assert not s.events
    s.verification = Verification(status="verified", party_id="P9", role="policyholder")
    disclosure_event(s, ReplyBrief(phase="p", goal="g",
                                   allowed_facts={"claim_id": "CL-2048", "net_pay": "$0.00"}))
    assert s.events[-1].type == "disclosed" and s.events[-1].data["facts"] == ["claim_id", "net_pay"]
    assert s.events[-1].data["role"] == "policyholder"
