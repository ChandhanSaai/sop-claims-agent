# Task tracker

One row per task, one PR per task. Status values: `todo`, `in progress`, `in review`, `merged`, `blocked`.
Waves run with at most five agents in parallel; a task starts when every task it depends on has passed review (its branch may still be awaiting merge, so PRs are stacked: each PR's base is the branch it was built on and is retargeted to `main` once that branch merges).
Plan with per-task steps: `docs/superpowers/plans/2026-10-07-sop-claims-agent.md`. Spec: `docs/superpowers/specs/2026-10-07-sop-claims-agent-design.md`.

Merge order for the reviewer: merge PRs in task order (T01, T02, T03, then the five wave-3 PRs, then T09, T10, T11, T12). Delete the head branch on merge so the next stacked PR retargets to `main`.

## Must

| ID | Task | Wave | Model | Depends on | Status | PR | Notes |
|---|---|---|---|---|---|---|---|
| T01 | Repo scaffold, contracts (Settings, TurnAnalysis, ReplyBrief), JSON logging with redaction, /healthz, fixtures, golden-transcript replay fixtures | 0 | Opus 5.5 | - | in review | #1 | review found 2 plan-mandated logging bugs; fix in progress |
| T02 | Data layer: typed fixtures, normalization, lookup by any identifier, pass/fail verification, claims filter, guideline lookup, consent simulator, outbox | 1 | Opus 5.5 | T01 | in progress | | base branch task/T01-scaffold |
| T03 | Engine core: session state, memory with provenance, pending-ask mapping, brief merge, affect policies, VERIFY_ID handler | 2 | Fable 5.1 | T02 | todo | | imports app.data |
| T04 | LLM layer: Reader (structured output), Writer (cached two-block system, fallbacks), FakeLLM | 3 | Opus 5.5 | T03 | todo | | imports app.engine.briefs, app.engine.state |
| T05 | HTTP API, session store, access-token gate, chat UI with SOP inspector | 3 | Opus 5.5 | T03 | todo | | imports app.engine.state |
| T06 | RESOLVE_INTENT (disambiguation, decoy) and PROCESS_CASE (grounded facts, deadlines, alternatives, anything-else) | 3 | Fable 5.1 | T02, T03 | todo | | |
| T07 | Cross-cutting policies: scope guard ladder, non-terminal escalation with hand-off packet, meta and mixed turns, injection flag | 3 | Opus 5.5 | T03 | todo | | |
| T08 | POST_PROCESS: email offer once, code-built summary draft, confirm, outbox, route back | 3 | Opus 5.5 | T02, T03 | todo | | |
| T09 | Output guard (echo-aware), redacted per-turn trace, disclosure events | 4 | Opus 5.5 | T03, T04 | todo | | |
| T10 | End-to-end integration: ConversationService, replay runner, both golden transcripts green, HTTP integration test | 5 | Fable 5.1 | T04, T05, T06, T07, T08, T09 | todo | | |
| T11 | Replay suite for every brief scenario, zero-tolerance leak check, CI gating | 6 | Opus 5.5 | T10 | todo | | |
| T12 | Dockerfile, compose, CLI, transcript renderer, README with golden transcripts, demo recording | 6 | Opus 5.5 | T10 | todo | | |

## Stretch (after T12 is merged, in this order; each gets its own plan)

| ID | Task | Model | Depends on | Status | PR | Notes |
|---|---|---|---|---|---|---|
| S01 | Representative and consent sub-flow (approve and timeout scenarios) | Opus 5.5 | T10 | todo | | fixtures already exist |
| S02 | Live persona evaluations with pass^k and an LLM judge (opt-in) | Opus 5.5 | T11 | todo | | |
| S03 | Hosted demo behind DEMO_ACCESS_TOKEN | Opus 5.5 | T12 | todo | | |
| S04 | Abuse handling policy | Opus 5.5 | T07 | todo | | |
| S05 | OpenAI provider adapter | Opus 5.5 | T04 | todo | | |

## Deferred findings (for the final whole-branch review)

- T01: `anthropic_api_key` and `demo_access_token` as plain `str` appear in `repr(Settings)`; consider `SecretStr`.
- T01: CI actions emit Node 20 deprecation warnings; bump `actions/checkout` and `actions/setup-python` majors after confirming the versions exist.
- T01: log redaction does not cover bare 4-digit ID fragments; the control is that no code path logs raw caller text or identity slot values (trace masks them).

## Log

- 2026-10-07: spec v0.5 frozen, plan written, tracker created.
- 2026-10-07: T01 implemented (PR #1); review found two plan-mandated logging defects (redaction filter breaks `%d` formatting; exception text unredacted), fix dispatched. Dependency correction: T03 needs T02, T04 and T05 need T03, so the waves were re-cut (above). T02 started on a worktree off the T01 branch.
