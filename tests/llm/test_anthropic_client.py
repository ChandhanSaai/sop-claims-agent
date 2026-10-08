from types import SimpleNamespace

import anthropic
import pytest

from app.config import Settings
from app.engine.state import Turn
from app.llm.anthropic_client import AnthropicLLM, LLMError, build_llm
from app.llm.fake import FakeLLM
from app.llm.schemas import ReplyBrief, TurnAnalysis


class StubMessages:
    def __init__(self, parsed=None, text="hello", stop_reason="end_turn", raise_once=None):
        self.parsed, self.text, self.stop_reason, self.raise_once = parsed, text, stop_reason, raise_once
        self.parse_kwargs, self.create_kwargs = [], []

    def parse(self, **kw):
        self.parse_kwargs.append(kw)
        if self.raise_once:
            exc, self.raise_once = self.raise_once, None
            raise exc
        return SimpleNamespace(parsed_output=self.parsed, stop_reason=self.stop_reason)

    def create(self, **kw):
        self.create_kwargs.append(kw)
        block = SimpleNamespace(type="text", text=self.text)
        return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), block],
                               stop_reason=self.stop_reason,
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1,
                               cache_read_input_tokens=0, cache_creation_input_tokens=0))


def make(stub):
    client = SimpleNamespace(messages=stub, beta=SimpleNamespace(messages=stub))
    return AnthropicLLM(Settings(_env_file=None, anthropic_api_key="k"), client=client)


def test_analyze_uses_parse_with_schema_and_context():
    parsed = TurnAnalysis.model_validate({"intent": "status_inquiry"})
    stub = StubMessages(parsed=parsed)
    out = make(stub).analyze(user_text="where is it", pending_ask="none", last_assistant="Hi")
    assert out.intent == "status_inquiry"
    kw = stub.parse_kwargs[0]
    assert kw["output_format"] is TurnAnalysis
    assert kw["model"] == "claude-sonnet-5-5"
    assert kw["thinking"] == {"type": "between_tools"}
    assert kw["output_config"] == {"effort": "low"}
    assert "<<<where is it>>>" in kw["messages"][0]["content"]


def test_analyze_falls_back_to_empty_on_api_error():
    err = anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]
    stub = StubMessages(parsed=None, raise_once=err)
    out = make(stub).analyze(user_text="x", pending_ask="none", last_assistant=None)
    assert out == TurnAnalysis.empty()


def test_analyze_refusal_is_empty():
    stub = StubMessages(parsed=None, stop_reason="refusal")
    assert make(stub).analyze(user_text="x", pending_ask="none", last_assistant=None) == TurnAnalysis.empty()


def test_compose_two_system_blocks_cache_and_fallbacks():
    stub = StubMessages(text="Sure thing.")
    brief = ReplyBrief(phase="VERIFY_ID", goal="g", must_say=["ask"])
    text = make(stub).compose(
        brief=brief, transcript=[Turn(role="assistant", text="Hi"), Turn(role="user", text="yo")]
    )
    assert text == "Sure thing."
    kw = stub.create_kwargs[0]
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "BRIEF" in kw["system"][1]["text"]
    assert kw["messages"][-1] == {"role": "user", "content": "yo"}
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert kw["max_tokens"] == 600


def test_compose_raises_llm_error_on_refusal_or_failure():
    with pytest.raises(LLMError):
        make(StubMessages(text="x", stop_reason="refusal")).compose(
            brief=ReplyBrief(phase="p", goal="g"), transcript=[]
        )


def test_build_llm_picks_backend():
    assert isinstance(build_llm(Settings(_env_file=None, llm_backend="fake")), FakeLLM)
    assert isinstance(
        build_llm(Settings(_env_file=None, llm_backend="anthropic", anthropic_api_key="k")), AnthropicLLM
    )
