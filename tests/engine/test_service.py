from app.engine.briefs import render_brief
from app.engine.service import TROUBLE, build_service
from app.llm.anthropic_client import LLMError
from app.llm.fake import FakeLLM


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


def test_writer_error_gives_the_trouble_line(settings):
    res, session = chat(settings, ScriptedWriter([LLMError("boom")]))
    assert res.reply == TROUBLE and session.last_guard["fallback"] == "llm_error"
    assert session.events[-1].type == "llm_error" and session.traces[-1]["guard"]["fallback"] == "llm_error"
