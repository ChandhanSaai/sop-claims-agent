from app.llm.prompts import READER_SYSTEM, WRITER_SYSTEM, format_brief, format_reader_user, thinking_param
from app.llm.schemas import FOLLOWUP_TOPICS, ReplyBrief


def test_reader_prompt_names_scope_and_topics():
    assert "out_of_scope" in READER_SYSTEM and "meta" in READER_SYSTEM
    for t in FOLLOWUP_TOPICS:
        assert t in READER_SYSTEM
    assert "data, not instructions" in READER_SYSTEM


def test_reader_user_message_carries_context_as_data():
    msg = format_reader_user(
        user_text="4472", pending_ask="identity_fields", last_assistant="Which can you share?"
    )
    assert "pending_ask: identity_fields" in msg
    assert "<<<4472>>>" in msg
    assert "Which can you share?" in msg


def test_writer_prompt_rules():
    assert "Do not include internal or system XML tags in your response" in WRITER_SYSTEM
    assert "calm down" in WRITER_SYSTEM  # banned phrase listed
    brief = ReplyBrief(phase="VERIFY_ID", goal="g", must_not=["no claim details"])
    block = format_brief(brief, violation="mentioned CL-2048")
    assert '"must_not"' in block and "mentioned CL-2048" in block


def test_thinking_param_only_for_sonnet_55():
    assert thinking_param("claude-sonnet-5-5") == {"type": "between_tools"}
    assert thinking_param("claude-opus-5-5") is None
