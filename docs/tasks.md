# Task tracker

One row per task, one PR per task. Status values: `todo`, `in progress`, `in review`, `merged`, `blocked`.
Waves run with at most five agents in parallel; a task starts when every task it depends on has passed review (its branch may still be awaiting merge, so PRs are stacked: each PR's base is the branch it was built on and is retargeted to `main` once that branch merges).
Plan with per-task steps: `docs/superpowers/plans/2026-10-07-sop-claims-agent.md`. Spec: `docs/superpowers/specs/2026-10-07-sop-claims-agent-design.md`.

Merge order for the reviewer: merge PRs in task order (T01, T02, T03, then the five wave-3 PRs, then T09, T10, T11, T12). Delete the head branch on merge so the next stacked PR retargets to `main`.

## Must

| ID | Task | Wave | Model | Depends on | Status | PR | Notes |
|---|---|---|---|---|---|---|---|
| T01 | Repo scaffold, contracts (Settings, TurnAnalysis, ReplyBrief), JSON logging with redaction, /healthz, fixtures, golden-transcript replay fixtures | 0 | Opus 5.5 | - | ready to merge | #1 | two plan-mandated logging bugs fixed in 75e5e4c; re-review approved |
| T02 | Data layer: typed fixtures, normalization, lookup by any identifier, pass/fail verification, claims filter, guideline lookup, consent simulator, outbox | 1 | Opus 5.5 | T01 | ready to merge | #2 | lookup-precedence bug fixed in a725aea; re-review approved; base task/T01-scaffold |
| T03 | Engine core: session state, memory with provenance, pending-ask mapping, brief merge, affect policies, VERIFY_ID handler | 2 | Fable 5.1 | T02 | ready to merge | #3 | two plan-mandated verify_id bugs fixed in 8eafb0d; re-review approved; base task/T02-data-layer |
| T04 | LLM layer: Reader (structured output), Writer (cached two-block system, fallbacks), FakeLLM | 3 | Opus 5.5 | T03 | ready to merge | #6 | Writer window, stop-reason and Reader-retry defects fixed in 1d23937; re-review approved; real-API smoke test deferred to T10 |
| T05 | HTTP API, session store, access-token gate, chat UI with SOP inspector | 3 | Opus 5.5 | T03 | ready to merge | #5 | per-session lock added in cdfc92c; re-review approved; base task/T03-engine-core |
| T06 | RESOLVE_INTENT (disambiguation, decoy) and PROCESS_CASE (grounded facts, deadlines, alternatives, anything-else) | 3 | Fable 5.1 | T02, T03 | ready to merge | #7 | two off-path defects plus a same-turn double switch fixed (b60af46, d03f9d2); re-review approved; adds pythonpath to pyproject |
| T07 | Cross-cutting policies: scope guard ladder, non-terminal escalation with hand-off packet, meta and mixed turns, injection flag | 3 | Opus 5.5 | T03 | ready to merge | #4 | three fix rounds on the brief's ladder code (a1d94dd, 0d54fcc, 019ebb3); final re-review approved |
| T08 | POST_PROCESS: email offer once, code-built summary draft, confirm, outbox, route back | 4 | Opus 5.5 | T06 | ready to merge | #8 | three fix rounds (7d307a3, fc5d13c, 4f07713); final re-review approved; base task/T06-intent-and-case |
| T09 | Output guard (echo-aware), redacted per-turn trace, disclosure events | 4 | Opus 5.5 | T03, T04 | ready to merge | #9 | two fix rounds on the brief's guard/trace code (fb8e2b8, 68c732a); re-review approved; base task/T04-llm-layer |
| T10 | End-to-end integration: ConversationService, replay runner, both golden transcripts green, HTTP integration test | 5 | Fable 5.1 | T04, T05, T06, T07, T08, T09 | ready to merge | #10 | both golden transcripts green with the FakeLLM; regenerate-failure observability fixed (ad12570); PR against main, merge after #1-#9; live smoke test skipped (no key) |
| T11 | Replay suite for every brief scenario, zero-tolerance leak check, CI gating | 6 | Opus 5.5 | T10 | in review | #11 | 9 scenarios + 9 leak checks green; base task/T10-integration |
| T12 | Dockerfile, compose, CLI, transcript renderer, README with golden transcripts, demo recording | 6 | Opus 5.5 | T10 | in progress | | base task/T10-integration |

## Stretch (after T12 is merged, in this order; each gets its own plan)

| ID | Task | Model | Depends on | Status | PR | Notes |
|---|---|---|---|---|---|---|
| S01 | Representative and consent sub-flow (approve and timeout scenarios) | Opus 5.5 | T10 | todo | | fixtures already exist |
| S02 | Live persona evaluations with pass^k and an LLM judge (opt-in) | Opus 5.5 | T11 | todo | | |
| S03 | Hosted demo behind DEMO_ACCESS_TOKEN | Opus 5.5 | T12 | todo | | |
| S04 | Abuse handling policy | Opus 5.5 | T07 | todo | | |
| S05 | OpenAI provider adapter | Opus 5.5 | T04 | todo | | |

## Deferred findings (for the final whole-branch review)

- T10 (minors): `guard_ok: false` can never pass in the replay runner because every `ok: False` result also carries a `fallback`; the live-model transcript has not been run (no API key available).
- T09 (re-review minors): a partial raw DOB in memory (for example "March") over-fires the echo rule (gate on two digit runs; fixed in T10); a full DOB that `parse_dob` cannot parse is caught only when echoed verbatim (ordinal support in `parse_dob`; fixed in T10); document names in a different word order pass before verification; the VERIFY_ID re-ask example "15 March 1985" is Margaret's real DOB (changed in T10).
- T08 (re-review minors): a contradictory answer at the offer step builds the draft without sending; a close beats a bare claim switch in POST_PROCESS while PROCESS_CASE gives the switch precedence; a yes that also carries a question drops the question.
- T06 (re-review minors): a same-turn double switch is still reachable when a disambiguation answer carries a hint that contradicts its own pick (one-line guard in `resolve_intent`); no positive test for the words-plus-document trigger; `hints_match` logic exists in both handlers; the trace will record the mutated `switch_claim=False`.
- T04 (re-review minors): a 114-char test line exempted by ruff's trailing-comment rule; window tests do not pin the slice-then-trim order; a refusal with partial text costs an extra Reader call; `include_input=False` on the retry note is untested.
- T05 (re-review minors): outbox fetch is silent on a non-OK status; no guard against a stale reply after New conversation; lock waits are unbounded and tie up worker threads (timeout plus 409 is the upgrade); no test that `put()` sweeps expired sessions.
- T03 (re-review minors): policy number is not part of the attempt fingerprint; `parse_dob` does not accept ordinal suffixes ("March 15th, 1985"); a bad DOB slot is never cleared, so the caller must restate it before other identifiers can proceed; the raw-value fingerprint fallback has no test; the `verified` event logs the provided count rather than the matched names.
- T03 (review minors): `pass2` replaces a DOB re-ask with the human offer while the caller stays frustrated (single `ask` slot); `find` first-key-wins can let a wrong phone that collides with another record shadow a correct name lookup; empty-string hints are stored.
- T02 (re-review minor): no test pins `find` precedence when two supplied keys hit different records.
- T01 (re-review minors): the redaction fallback can still raise on an object whose `__str__` fails and has no committed test; the new logging test's cleanup is not in a `finally`; the caplog survival check counts records and is sensitive to `--log-level`; importing `anthropic` with `ANTHROPIC_LOG` set installs a plain-text root handler via `basicConfig` that bypasses redaction (do not set `ANTHROPIC_LOG` in production; T04 documents this).

- T01: `anthropic_api_key` and `demo_access_token` as plain `str` appear in `repr(Settings)`; consider `SecretStr`.
- T01: CI actions emit Node 20 deprecation warnings; bump `actions/checkout` and `actions/setup-python` majors after confirming the versions exist.
- T01: log redaction does not cover bare 4-digit ID fragments; the control is that no code path logs raw caller text or identity slot values (trace masks them).

## Log

- 2026-10-07: spec v0.5 frozen, plan written, tracker created.
- 2026-10-08: wave 3 complete: T04, T05, T06, T07, T08, T09 all approved after fix rounds; integration branch assembled (131 tests before T09); T10 started on Fable.
- 2026-10-07: T03 review found two plan-mandated verify_id defects (fingerprint on raw strings; unmatched identifiers marked verified), fix dispatched; T04, T05, T06, T07 started in parallel off the T03 tip; T08 re-sequenced after T06.
- 2026-10-07: T02 implemented (PR #2); review found the plan's `find` stopped at the first identifier present (lockout risk), fixed with four minors; re-review approved. T03 implemented on Fable (PR #3), in review.
- 2026-10-07: T01 implemented (PR #1); review found two plan-mandated logging defects (redaction filter breaks `%d` formatting; exception text unredacted), fix dispatched. Dependency correction: T03 needs T02, T04 and T05 need T03, so the waves were re-cut (above). T02 started on a worktree off the T01 branch.
