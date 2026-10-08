from pydantic import BaseModel, Field

from app.llm.schemas import ReplyBrief


class HandlerResult(BaseModel):
    brief: ReplyBrief
    advanced: bool = False       # the handler moved session.phase forward
    needs_input: bool = True     # the handler asked the caller something; stop the chain
    transition_fact: str | None = None
    transition_facts: dict[str, str] = Field(default_factory=dict)


def merge_briefs(results: list[HandlerResult]) -> ReplyBrief:
    """Earlier handlers contribute only their transition fact (and its values as allowed facts).
    Everything else comes from the last handler."""
    last = results[-1].brief
    facts: dict[str, str] = {}
    lead: list[str] = []
    for r in results[:-1]:
        if r.transition_fact:
            lead.append(r.transition_fact)
        facts.update(r.transition_facts)
        facts.update(r.brief.allowed_facts)
    facts.update(last.allowed_facts)
    return last.model_copy(update={"must_say": lead + list(last.must_say), "allowed_facts": facts})


def render_brief(brief: ReplyBrief) -> str:
    """Deterministic plain-text rendering. Used by FakeLLM and as the guard's safe fallback."""
    parts: list[str] = []
    if brief.acknowledge:
        parts.append(brief.acknowledge)
    parts.extend(brief.must_say)
    for key, val in brief.allowed_facts.items():
        parts.append(f"{key.replace('_', ' ')}: {val}")
    if brief.options:
        parts.append("Options: " + "; ".join(brief.options))
    if brief.ask:
        parts.append(brief.ask)
    return " ".join(p if p.endswith((".", "?", "!")) else p + "." for p in parts)
