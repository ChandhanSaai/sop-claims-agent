from app.engine.briefs import HandlerResult, merge_briefs, render_brief
from app.llm.schemas import ReplyBrief


def test_merge_briefs_leads_with_transition_fact_and_takes_the_rest_from_the_last_handler():
    first = HandlerResult(
        brief=ReplyBrief(phase="VERIFY_ID", goal="Verify.", must_say=["Only the first handler says this."],
                         must_not=["first rule"], ask="First ask?", allowed_facts={"verified": "yes"}),
        advanced=True, needs_input=False,
        transition_fact="Identity verification is complete.", transition_facts={"party": "policyholder"},
    )
    last = HandlerResult(
        brief=ReplyBrief(phase="RESOLVE_INTENT", goal="Pick the claim.", must_say=["I found your claim."],
                         must_not=["last rule"], ask="Which claim?", allowed_facts={"case_id": "CL-2048"}),
    )
    merged = merge_briefs([first, last])
    assert merged.must_say == ["Identity verification is complete.", "I found your claim."]
    assert merged.allowed_facts == {"verified": "yes", "party": "policyholder", "case_id": "CL-2048"}
    assert (merged.phase, merged.goal, merged.ask, merged.must_not) == (
        "RESOLVE_INTENT", "Pick the claim.", "Which claim?", ["last rule"]
    )


def test_render_brief_order_is_acknowledge_must_say_facts_options_ask():
    brief = ReplyBrief(phase="PROCESS_CASE", goal="Answer.", acknowledge="I understand",
                       must_say=["Here is what I found.", "It was denied"],
                       allowed_facts={"case_id": "CL-2048", "denial_reason": "missing receipt"},
                       options=["upload it", "mail it"], ask="Which works for you?")
    assert render_brief(brief) == (
        "I understand. Here is what I found. It was denied. case id: CL-2048. "
        "denial reason: missing receipt. Options: upload it; mail it. Which works for you?"
    )
