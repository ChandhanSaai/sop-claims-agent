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


def _build(session_id="abc", analysis=None, brief=None):
    return TraceRecord.build(
        session_id=session_id, turn=1, phase_before="VERIFY_ID", phase_after="VERIFY_ID", pending_ask="dob",
        analysis=analysis or TurnAnalysis(), changed_slots=[], brief=brief or ReplyBrief(phase="p", goal="g"),
        guard={"ok": True, "violations": []}, reader_model="r", writer_model="w", latency_ms=1, reply_text="")


def test_trace_record_masks_correction_values():
    rec = _build(analysis=TurnAnalysis.model_validate(
        {"corrections": [{"slot": "dob", "new_value": "1985-03-16"}]}))
    assert rec.analysis["corrections"] == [{"slot": "dob", "new_value": "******"}]
    assert "1985-03-16" not in rec.model_dump_json()


def test_trace_writer_redacts_free_text_but_keeps_ts_and_session_id(tmp_path):
    sid = "0123456789abcdef0123456789abcdef"
    brief = ReplyBrief(phase="p", goal="g", allowed_facts={"email_on_file": "margaret@email.com"})
    rec = _build(session_id=sid, brief=brief)
    TraceWriter(tmp_path).write(rec)
    data = json.loads((tmp_path / f"{sid}.jsonl").read_text())
    assert data["ts"] == rec.ts and data["session_id"] == sid
    assert data["brief"]["allowed_facts"]["email_on_file"] == "[REDACTED]"
