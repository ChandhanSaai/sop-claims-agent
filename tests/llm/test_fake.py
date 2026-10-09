from app.engine.state import Turn
from app.llm.fake import FakeLLM
from app.llm.schemas import ReplyBrief, TurnAnalysis


def test_fake_replays_queued_analyses_then_empty():
    a = TurnAnalysis.model_validate({"intent": "status_inquiry"})
    llm = FakeLLM([a])
    assert llm.analyze(user_text="x", pending_ask="none", last_assistant=None).intent == "status_inquiry"
    assert llm.analyze(user_text="y", pending_ask="none", last_assistant=None).intent == "none"


def test_fake_compose_renders_brief_deterministically():
    brief = ReplyBrief(phase="PROCESS_CASE", goal="g", must_say=["Verification is complete"],
                       allowed_facts={"claim_id": "CL-2048"}, ask="Anything else?")
    text = FakeLLM().compose(brief=brief, transcript=[Turn(role="user", text="hi")])
    assert text == "Verification is complete. claim id: CL-2048. Anything else?"
