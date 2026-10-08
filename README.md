# SOP-Guided Insurance Claims Support Agent

A chat agent for an insurance claims support line that follows a fixed four-phase SOP
(VERIFY_ID -> RESOLVE_INTENT -> PROCESS_CASE -> POST_PROCESS) enforced in code, with an LLM
used only to understand the caller and to phrase replies.

Status: design complete, implementation starting.

- Design spec: `docs/superpowers/specs/2026-10-07-sop-claims-agent-design.md`
- Research report behind the design: `docs/research/report.md`
- Task tracker: `docs/tasks.md` (added with the implementation plan)
