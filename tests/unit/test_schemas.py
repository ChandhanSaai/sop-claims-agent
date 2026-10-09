import pytest
from pydantic import ValidationError

from app.config import Settings
from app.llm.schemas import FOLLOWUP_TOPICS, INTENTS, ReplyBrief, TurnAnalysis


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for name in (
        "READER_MODEL", "WRITER_MODEL", "VERIFY_MIN_FIELDS", "VERIFY_REQUIRE_STRONG_FIELD",
        "VERIFY_MAX_ATTEMPTS", "LLM_BACKEND", "FIXTURES_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    s = Settings(_env_file=None)
    assert s.reader_model == "claude-sonnet-5-5"
    assert s.writer_model == "claude-sonnet-5-5"
    assert s.verify_min_fields == 3
    assert s.verify_require_strong_field is False
    assert s.verify_max_attempts == 3
    assert s.llm_backend == "anthropic"
    assert s.fixtures_dir.name == "fixtures"


def test_settings_read_env(monkeypatch):
    monkeypatch.setenv("VERIFY_MIN_FIELDS", "4")
    monkeypatch.setenv("DEMO_ACCESS_TOKEN", "secret")
    s = Settings(_env_file=None)
    assert s.verify_min_fields == 4
    assert s.demo_access_token == "secret"


def test_empty_analysis_is_neutral():
    a = TurnAnalysis.empty()
    assert a.intent == "none"
    assert a.scope == "in_scope"
    assert a.caller_role == "unknown"
    assert a.affect.frustration == 0
    assert a.requests.confirmation == "unspecified"
    assert a.corrections == []


def test_analysis_rejects_unknown_fields_and_bad_values():
    with pytest.raises(ValidationError):
        TurnAnalysis.model_validate({"identity": {"full_name": "x", "nickname": "y"}})
    with pytest.raises(ValidationError):
        TurnAnalysis.model_validate({"affect": {"frustration": 7}})
    with pytest.raises(ValidationError):
        TurnAnalysis.model_validate({"intent": "refund_request"})


def test_taxonomies_match_fixture_and_brief():
    assert set(INTENTS) == {
        "status_inquiry", "denial_question", "document_submission",
        "next_steps", "general_claim_question", "none",
    }
    assert "missing_required_material_alternatives" in FOLLOWUP_TOPICS
    assert "none" in FOLLOWUP_TOPICS


def test_reply_brief_defaults():
    b = ReplyBrief(phase="VERIFY_ID", goal="ask for identifiers")
    assert b.tone == "neutral"
    assert b.allowed_facts == {}
    assert b.offer_human is False
    assert b.verbatim is None
