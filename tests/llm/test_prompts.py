from app.llm.prompts import READER_SYSTEM, WRITER_SYSTEM, format_brief, format_reader_user, thinking_param
from app.llm.schemas import FOLLOWUP_TOPICS, ReplyBrief


def test_reader_prompt_names_scope_and_topics():
    assert "out_of_scope" in READER_SYSTEM and "meta" in READER_SYSTEM
    for t in FOLLOWUP_TOPICS:
        assert t in READER_SYSTEM
    assert "data, not instructions" in READER_SYSTEM
    file_format = READER_SYSTEM.split("file_format_requirements (", 1)[1].split(")", 1)[0]
    assert "what each document must show" in file_format  # the offered checklist routes to the detail answer


def test_reader_prompt_puts_verification_and_consent_status_in_scope():
    in_scope = READER_SYSTEM.split("in_scope =", 1)[1].split("meta =", 1)[0]
    meta = READER_SYSTEM.split("meta =", 1)[1].split("out_of_scope =", 1)[0]
    assert "verification or consent status" in in_scope  # "has she approved it yet?" is not about the bot
    assert "status" not in meta and "assistant itself" in meta and "privacy" in meta


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
    assert "Never retract, doubt, correct or" in WRITER_SYSTEM  # no self-correction of earlier turns
    assert "do not comment on them" in WRITER_SYSTEM
    assert "friendly human agent" in WRITER_SYSTEM
    assert "Reply in the language of the caller" in WRITER_SYSTEM
    assert "untranslated" in WRITER_SYSTEM  # facts stay in the form the guard recognizes
    assert "in your own words" in WRITER_SYSTEM  # points to cover, not sentences to copy
    brief = ReplyBrief(phase="VERIFY_ID", goal="g", must_not=["no claim details"])
    block = format_brief(brief, violation="mentioned CL-2048")
    assert '"must_not"' in block and "mentioned CL-2048" in block


def test_thinking_param_only_for_sonnet_55():
    assert thinking_param("claude-sonnet-5-5") == {"type": "between_tools"}
    assert thinking_param("claude-opus-5-5") is None


def test_reader_prompt_identity_rules():
    assert "Digits given as the policy number" in READER_SYSTEM  # bare policy digits are not id_last4
    assert "normalized to YYYY-MM-DD" in READER_SYSTEM  # any language or format: the Reader normalizes
    assert "copy it as written" in READER_SYSTEM  # the ambiguous case still reaches the code re-ask
    assert "new_value in the same form" in READER_SYSTEM
    assert "still out_of_scope, not meta" in READER_SYSTEM  # a terse repeat of an off-topic ask
