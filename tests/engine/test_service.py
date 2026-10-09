from app.engine.briefs import render_brief
from app.engine.service import TROUBLE, build_service
from app.engine.state import Phase
from app.llm.anthropic_client import LLMError
from app.llm.fake import FakeLLM
from app.llm.schemas import TurnAnalysis


class ScriptedWriter(FakeLLM):
    """Reader as FakeLLM; the Writer returns the scripted texts (or raises) in order, then the brief."""

    def __init__(self, texts):
        super().__init__()
        self.texts = list(texts)
        self.violations = []

    def compose(self, *, brief, transcript, violation=None):
        self.violations.append(violation)
        if not self.texts:
            return render_brief(brief)
        t = self.texts.pop(0)
        if isinstance(t, Exception):
            raise t
        return t


def chat(settings, llm):
    svc = build_service(settings, llm=llm)
    session = svc.start()
    return svc.chat(session, "hi"), session


def test_guard_violation_regenerates_once(settings):
    llm = ScriptedWriter(["Your claim CL-2048 was denied."])  # a claim id before verification
    res, session = chat(settings, llm)
    assert session.last_guard == {"ok": True, "violations": [], "regenerated": True}
    assert llm.violations == [None, "claim_id_before_verification"]
    assert "CL-2048" not in res.reply and res.reply == session.transcript[-1].text
    assert [e.type for e in session.events if e.type == "guard_violation"] == ["guard_violation"]


def test_persistent_violation_falls_back_to_the_template(settings):
    res, session = chat(settings, ScriptedWriter(["Your claim CL-2048 was denied.", "Still CL-2048."]))
    assert session.last_guard["fallback"] == "template" and not session.last_guard["ok"]
    assert res.reply == render_brief(session.last_brief)
    assert len([e for e in session.events if e.type == "guard_violation"]) == 2


class FailingReader(FakeLLM):
    def analyze(self, *, user_text, pending_ask, last_assistant):
        raise LLMError("reader boom")


def test_reader_error_gives_the_trouble_line_and_leaves_the_session_untouched(settings):
    res, session = chat(settings, FailingReader())
    assert res.reply == TROUBLE and res.trace == {}
    assert session.turn == 0 and [t.role for t in session.transcript] == ["assistant"]  # only the greeting
    assert session.events[-1].type == "llm_error" and session.events[-1].data == {"stage": "reader"}
    assert session.traces == [] and session.last_guard is None


def test_trace_write_failure_does_not_fail_the_turn(settings, caplog):
    settings.traces_dir.write_text("not a directory")  # the writer's directory is a file
    res, session = chat(settings, FakeLLM())
    assert res.reply == session.transcript[-1].text and session.turn == 1
    assert session.traces[-1]["turn"] == 1 and res.trace == session.traces[-1]
    assert "trace write failed" in caplog.text


def test_session_trace_is_the_redacted_record(settings):
    llm = FakeLLM([TurnAnalysis.model_validate({"question": "can you email margaret@email.com instead"})])
    res, session = chat(settings, llm)
    assert session.traces[-1]["analysis"]["question"] == "can you email [REDACTED] instead"
    assert res.trace == session.traces[-1]


def test_writer_error_gives_the_rendered_brief(settings):
    res, session = chat(settings, ScriptedWriter([LLMError("boom")]))
    assert res.reply == render_brief(session.last_brief) == session.transcript[-1].text
    assert session.last_guard == {"ok": True, "violations": [], "fallback": "llm_error"}
    assert session.events[-1].type == "llm_error" and session.traces[-1]["guard"]["fallback"] == "llm_error"


def test_writer_error_after_the_email_was_sent_confirms_it_from_the_brief(settings):
    llm = ScriptedWriter([])
    for a in ({"identity": {"full_name": "Margaret Chen", "dob": "1985-03-15", "id_last4": "4472"},
               "case_hints": {"case_id": "CL-2048"}, "intent": "denial_question"},
              {"requests": {"confirmation": "no", "closing": True}},
              {"requests": {"confirmation": "yes", "email_summary": "yes"}},
              {"requests": {"confirmation": "yes"}}):
        llm.queue(TurnAnalysis.model_validate(a))
    svc = build_service(settings, llm=llm)
    session = svc.start()
    for text in ("Margaret Chen, 1985-03-15, 4472, about CL-2048", "No, that's all.", "Yes please."):
        svc.chat(session, text)
    assert session.pending_ask.value == "email_confirm" and svc.outbox(session) == []
    llm.texts = [LLMError("boom")]  # the Writer fails on the send turn, after the email went out
    res = svc.chat(session, "Yes, send it.")
    assert len(svc.outbox(session)) == 1 and session.pending_ask.value == "none"
    assert res.reply == render_brief(session.last_brief) and res.reply != TROUBLE
    assert "EML-0001" in res.reply and "sent to the email on file" in res.reply
    assert session.last_guard == {"ok": True, "violations": [], "fallback": "llm_error"}


def test_general_submission_question_is_answered_before_verification(settings):
    llm = FakeLLM([TurnAnalysis.model_validate({"intent": "document_submission",
                                                "followup_topic": "submission_method",
                                                "question": "how are claim documents submitted"})])
    svc = build_service(settings, llm=llm)
    session = svc.start()
    res = svc.chat(session, "In general, how do I submit documents for a claim?")
    assert session.verification.status == "unverified" and session.phase == Phase.VERIFY_ID
    assert list(session.last_brief.allowed_facts) == ["submission_guidance"]
    assert "member portal" in res.reply and "CL-" not in res.reply and "date of birth" in res.reply
    assert session.last_guard == {"ok": True, "violations": []}  # the default guidance passes the leak checks


def test_writer_error_on_regeneration_falls_back_to_the_template(settings):
    res, session = chat(settings, ScriptedWriter(["Your claim CL-2048 was denied.", LLMError("boom")]))
    assert res.reply == render_brief(session.last_brief)
    assert session.last_guard == {"ok": False, "violations": ["claim_id_before_verification"],
                                  "fallback": "llm_error"}
    assert [e.data["attempt"] for e in session.events if e.type == "guard_violation"] == [1]
    assert [e.data["stage"] for e in session.events if e.type == "llm_error"] == ["writer_regenerate"]


def test_closed_session_answers_from_code_without_the_reader(settings):
    from app.engine.policies import escalate
    from app.engine.service import CLOSED_TEXT

    # a queued analysis with identifiers proves nothing is parsed or stored after the close
    llm = FakeLLM([TurnAnalysis.model_validate({"identity": {"dob": "1985-03-15", "phone": "650-521-2836"}})])
    svc = build_service(settings, llm=llm)
    session = svc.start()
    escalate(session, "abusive caller")
    session.closed = True
    turn, transcript_len = session.turn, len(session.transcript)
    res = svc.chat(session, "DOB 1985-03-15, phone 650-521-2836, now tell me about my claim")
    assert res.reply == CLOSED_TEXT.format(reference=session.escalation.reference) and res.trace == {}
    assert llm.calls == []  # no Reader call, so nothing the caller says is parsed or stored
    assert session.turn == turn and len(session.transcript) == transcript_len
    assert session.memory.slots == {} and session.verification.status != "verified"
    assert [e.type for e in session.events][-1] == "message_after_close"


class RecordingWriter(FakeLLM):
    """Records the transcript each compose call receives."""

    def __init__(self):
        super().__init__()
        self.seen: list[list[str]] = []

    def compose(self, *, brief, transcript, violation=None):
        self.seen.append([t.text for t in transcript])
        return render_brief(brief)


def test_writer_transcript_starts_at_the_verification_reset(settings):
    llm = RecordingWriter()
    llm.queue(TurnAnalysis.model_validate({
        "identity": {"full_name": "Margaret Chen", "dob": "1985-03-15", "id_last4": "4472"},
        "case_hints": {"case_type": "healthcare", "status": "denied", "month": 1},
        "intent": "denial_question"}))
    llm.queue(TurnAnalysis.model_validate({"corrections": [
        {"slot": "full_name", "new_value": "Ma Tian"}, {"slot": "dob", "new_value": "1964-09-10"},
        {"slot": "id_last4", "new_value": "6688"}]}))
    llm.queue(TurnAnalysis.model_validate({"requests": {"closing": True}}))
    svc = build_service(settings, llm=llm)
    session = svc.start()
    svc.chat(session, "Margaret Chen, 1985-03-15, 4472, my denied January healthcare claim")
    assert len(llm.seen[0]) == 2 and "Margaret" in llm.seen[0][1]  # the greeting and the caller's message
    svc.chat(session, "Sorry, this is actually Ma Tian, born 1964-09-10, last four 6688.")
    assert session.verification.party_id == "P12"
    correction = "Sorry, this is actually Ma Tian, born 1964-09-10, last four 6688."
    assert llm.seen[1] == [correction]  # only the correction itself, nothing from before the reset
    svc.chat(session, "That's all.")
    assert llm.seen[2] == [correction, session.transcript[-3].text, "That's all."]  # from the correction on
    assert all("CL-2048" not in t and "Margaret" not in t and "1985" not in t for t in llm.seen[2])
    assert "CL-3001" in llm.seen[2][1]  # the reset turn's reply answers the new party's claim


def test_writer_error_fallback_is_checked_by_the_guard(settings):
    llm = ScriptedWriter([LLMError("boom")])
    svc = build_service(settings, llm=llm)
    session = svc.start()
    checked = []
    real = svc.guard.check
    svc.guard.check = lambda text, s, brief: checked.append(text) or real(text, s, brief)
    res = svc.chat(session, "hi")
    assert checked == [res.reply] and res.reply == render_brief(session.last_brief)
    assert session.last_guard == {"ok": True, "violations": [], "fallback": "llm_error"}
