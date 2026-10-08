import logging

import anthropic
from pydantic import ValidationError

from app.config import Settings
from app.engine.state import Turn
from app.llm.base import LLM
from app.llm.fake import FakeLLM
from app.llm.prompts import READER_SYSTEM, WRITER_SYSTEM, format_brief, format_reader_user, thinking_param
from app.llm.schemas import ReplyBrief, TurnAnalysis

log = logging.getLogger(__name__)
TRANSCRIPT_WINDOW = 12  # last N turns the Writer sees


class LLMError(RuntimeError):
    """The model call failed or was refused; the service substitutes a templated reply."""


class AnthropicLLM:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None):
        self.settings = settings
        self.client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key, max_retries=2, timeout=30.0
        )

    def _common(self, model: str) -> dict:
        params: dict = {"model": model, "output_config": {"effort": "low"}}
        thinking = thinking_param(model)
        if thinking:
            params["thinking"] = thinking
        return params

    def _parse(self, content: str) -> "anthropic.types.ParsedMessage[TurnAnalysis]":
        return self.client.messages.parse(
            **self._common(self.settings.reader_model),
            max_tokens=1500,
            system=[{"type": "text", "text": READER_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            output_format=TurnAnalysis,
        )

    def analyze(self, *, user_text: str, pending_ask: str, last_assistant: str | None) -> TurnAnalysis:
        content = format_reader_user(
            user_text=user_text, pending_ask=pending_ask, last_assistant=last_assistant
        )
        try:
            try:
                resp = self._parse(content)
            except ValidationError as e:  # retry once, showing the model what failed
                log.warning("reader output failed validation; retrying once")
                errors = e.json(include_input=False, include_url=False)
                resp = self._parse(
                    f"{content}\n\nYour previous output failed validation: {errors}. "
                    "Return the schema again with valid values."
                )
        except (anthropic.APIConnectionError, anthropic.APIStatusError, ValidationError) as e:
            log.warning("reader call failed: %s", type(e).__name__)
            return TurnAnalysis.empty()
        if getattr(resp, "stop_reason", None) == "refusal" or resp.parsed_output is None:
            log.warning(
                "reader returned no parsed output (stop_reason=%s)", getattr(resp, "stop_reason", None)
            )
            return TurnAnalysis.empty()
        return resp.parsed_output

    def compose(self, *, brief: ReplyBrief, transcript: list[Turn], violation: str | None = None) -> str:
        window = transcript[-TRANSCRIPT_WINDOW:]
        while window and window[0].role != "user":  # the Messages API wants a user turn first
            window = window[1:]
        messages = [{"role": t.role, "content": t.text} for t in window]
        if not messages or messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": "(continue)"})
        try:
            resp = self.client.beta.messages.create(
                **self._common(self.settings.writer_model),
                max_tokens=600,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[
                    {"type": "text", "text": WRITER_SYSTEM, "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": format_brief(brief, violation)},
                ],
                messages=messages,
            )
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
            raise LLMError(f"writer call failed: {type(e).__name__}") from e
        if resp.stop_reason == "refusal":
            raise LLMError("writer refused")
        if resp.stop_reason != "end_turn":  # e.g. max_tokens: never send a truncated reply
            raise LLMError(f"writer stopped: {resp.stop_reason}")
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "").strip()
        if not text:
            raise LLMError("writer returned no text")
        return text


def build_llm(settings: Settings) -> LLM:
    if settings.llm_backend == "fake":
        return FakeLLM()
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Set it in .env, or set LLM_BACKEND=fake for the offline demo."
        )
    return AnthropicLLM(settings)
