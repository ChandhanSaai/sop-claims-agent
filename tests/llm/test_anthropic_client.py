import json
from types import SimpleNamespace

import anthropic
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.engine.state import Turn
from app.llm.anthropic_client import AnthropicLLM, LLMError, build_llm
from app.llm.fake import FakeLLM
from app.llm.schemas import ReplyBrief, TurnAnalysis


class StubMessages:
    def __init__(self, parsed=None, text="hello", stop_reason="end_turn", raises=()):
        self.parsed, self.text, self.stop_reason, self.raises = parsed, text, stop_reason, list(raises)
        self.parse_kwargs, self.create_kwargs = [], []

    def parse(self, **kw):
        self.parse_kwargs.append(kw)
        if self.raises:
            raise self.raises.pop(0)
        return SimpleNamespace(parsed_output=self.parsed, stop_reason=self.stop_reason)

    def create(self, **kw):
        self.create_kwargs.append(kw)
        if self.raises:
            raise self.raises.pop(0)
        block = SimpleNamespace(type="text", text=self.text)
        return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), block],
                               stop_reason=self.stop_reason,
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1,
                               cache_read_input_tokens=0, cache_creation_input_tokens=0))


def make(stub=None, client=None):
    # Explicit models: a READER_MODEL / WRITER_MODEL set in the shell must not change what is asserted.
    settings = Settings(_env_file=None, anthropic_api_key="k",
                        reader_model="claude-sonnet-5-5", writer_model="claude-sonnet-5-5")
    stub_client = SimpleNamespace(messages=stub, beta=SimpleNamespace(messages=stub))
    return AnthropicLLM(settings, client=client or stub_client)


def validation_error() -> ValidationError:
    return ValidationError.from_exception_data(
        "TurnAnalysis", [{"type": "missing", "loc": ("intent",), "input": {}}]
    )


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
    assert kw["max_tokens"] == 1500
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "<<<where is it>>>" in kw["messages"][0]["content"]


def test_analyze_falls_back_to_empty_on_api_error():
    err = anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]
    stub = StubMessages(parsed=None, raises=[err])
    out = make(stub).analyze(user_text="x", pending_ask="none", last_assistant=None)
    assert out == TurnAnalysis.empty()
    assert len(stub.parse_kwargs) == 1  # API errors are not retried here; the SDK already retries them


def test_analyze_refusal_is_empty():
    stub = StubMessages(parsed=None, stop_reason="refusal")
    assert make(stub).analyze(user_text="x", pending_ask="none", last_assistant=None) == TurnAnalysis.empty()


def test_analyze_retries_once_with_the_validation_error():
    parsed = TurnAnalysis.model_validate({"intent": "next_steps"})
    stub = StubMessages(parsed=parsed, raises=[validation_error()])
    assert make(stub).analyze(user_text="x", pending_ask="none", last_assistant=None) == parsed
    first, second = stub.parse_kwargs
    retry = second["messages"][0]["content"]
    assert retry.startswith(first["messages"][0]["content"])
    assert "failed validation" in retry and "Field required" in retry
    assert {**second, "messages": None} == {**first, "messages": None}  # otherwise the same arguments


@pytest.mark.parametrize(
    "second_error",
    [validation_error(), anthropic.APIConnectionError(request=None)],  # type: ignore[arg-type]
    ids=["validation_error", "api_error"],
)
def test_analyze_is_empty_when_the_retry_fails_too(second_error):
    parsed = TurnAnalysis.model_validate({"intent": "next_steps"})
    stub = StubMessages(parsed=parsed, raises=[validation_error(), second_error])
    assert make(stub).analyze(user_text="x", pending_ask="none", last_assistant=None) == TurnAnalysis.empty()
    assert len(stub.parse_kwargs) == 2


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
    assert kw["messages"][0]["role"] == "user"
    assert kw["messages"][-1] == {"role": "user", "content": "yo"}
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert kw["max_tokens"] == 600


@pytest.mark.parametrize(
    "stub_kwargs",
    [
        {"text": "x", "stop_reason": "refusal"},
        {"stop_reason": "max_tokens"},
        {"text": "  "},
        {"raises": [anthropic.APIConnectionError(request=None)]},  # type: ignore[arg-type]
        {"raises": [anthropic.APIStatusError(
            "boom", response=SimpleNamespace(status_code=500, headers={}, request=None),  # type: ignore[arg-type]
            body=None,
        )]},
    ],
    ids=["refusal", "max_tokens", "empty_text", "connection_error", "status_error"],
)
def test_compose_raises_llm_error_on_refusal_or_failure(stub_kwargs):
    with pytest.raises(LLMError):
        make(StubMessages(**stub_kwargs)).compose(brief=ReplyBrief(phase="p", goal="g"), transcript=[])


def test_real_sdk_accepts_the_exact_kwargs():
    """Both calls go through the real SDK over an in-memory transport, so no network is used."""
    httpx2 = pytest.importorskip("httpx2")  # anthropic 1.x requires an httpx2 client and rejects httpx ones
    if not hasattr(httpx2, "MockTransport"):
        pytest.skip("httpx2.MockTransport is unavailable")
    canned = '{"intent": "status_inquiry"}'
    sent = []

    def handler(request):
        sent.append(request)
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": canned}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    sdk = anthropic.Anthropic(api_key="k", base_url="https://api.anthropic.com", max_retries=0,
                              http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    llm = make(client=sdk)
    assert llm.analyze(user_text="hi", pending_ask="none", last_assistant=None).intent == "status_inquiry"
    reply = llm.compose(brief=ReplyBrief(phase="p", goal="g"), transcript=[Turn(role="user", text="hi")])
    assert reply == canned
    assert [r.url.path for r in sent] == ["/v1/messages", "/v1/messages"]
    reader, writer = (json.loads(r.content) for r in sent)
    assert reader["output_config"]["format"]["type"] == "json_schema"
    assert reader["output_config"]["effort"] == "low"
    assert sent[1].url.params["beta"] == "true"
    assert "server-side-fallback-2026-07-01" in sent[1].headers["anthropic-beta"]
    assert writer["fallbacks"] == "default"


def test_build_llm_picks_backend():
    assert isinstance(build_llm(Settings(_env_file=None, llm_backend="fake")), FakeLLM)
    assert isinstance(
        build_llm(Settings(_env_file=None, llm_backend="anthropic", anthropic_api_key="k")), AnthropicLLM
    )
