# S01: Representative and consent sub-flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a caller who acts for a policyholder (the fixtures ship David Chen, son of Margaret Chen) be verified as an authorized representative only after the policyholder's consent is obtained out of band (simulated), with the approve and timeout scenarios from `fixtures/consent_scenarios.json`, without ever verifying the caller as the policyholder from the policyholder's own identifiers.

**Architecture:** The representative branch inside the VERIFY_ID handler grows from a human-offer stub into a small sub-state machine driven by `session.consent` and `pending_ask = consent_wait`: collect the representative's name, relationship and the policyholder's name into memory slots; match against `RepresentativeRepo`; request consent once through `ConsentService`; poll once per turn; on approval set `verification` to verified with role `representative` and chain into RESOLVE_INTENT in the same turn; on timeout fall back to general information and a human offer. POST_PROCESS tells a representative that the summary goes to the policyholder's address. Disclosure events already carry `consent_id`.

**Tech Stack:** unchanged (Python, Pydantic, pytest, YAML replay fixtures).

**Spec:** `docs/superpowers/specs/2026-10-07-sop-claims-agent-design.md` section 7 (VERIFY_ID representative branch, stretch), section 8 (`RepresentativeRepo`, `ConsentService`), and the S01 acceptance criteria in the main plan's Stretch section.

## Global Constraints

- All constraints of the main plan apply (no LLM-set state, generic wording, no echoed identifiers, lines within 110 chars, no new dependencies, no attribution trailers).
- A representative is never verified through the policyholder's identifiers. The flag `verification.declared_representative` (added by the final fix pass, C2) stays set for the session once a representative role is declared; while set, the policyholder path is closed.
- Consent is requested at most once per session (`session.consent.consent_id`), polled at most once per turn, and its status is written only by code from `ConsentService.poll`.
- Wording for "representative not on file" must not reveal whether the policyholder exists: one generic sentence, identical whether the policyholder name or the representative name failed to match.
- The session's `scenario` selects the consent sequence (`default`: pending then approved; `timeout`: five pendings then timed out).
- Replay fixtures assert state and substrings only; never edit a fixture to match code.

## File Structure

```
app/engine/state.py              add memory slot names REP_SLOTS = ("rep_name", "rep_relationship", "rep_policyholder_name")
app/engine/memory.py             merge analysis.representative.* into those slots (provisional)
app/engine/phases/verify_id.py   replace the stub representative branch with the sub-flow
app/engine/phases/post_process.py  representative variant: summary goes to the policyholder's address; say so
app/engine/context.py            pending_ask == consent_wait: no mapping needed; ensure a bare "yes/no" is not misread
tests/engine/test_representative.py   unit and engine tests for the sub-flow
tests/replay/fixtures/representative_approved.yaml
tests/replay/fixtures/representative_timeout.yaml
ui/index.html                    consent scenario selector label restored to "Consent scenario"
README.md                        phase table row, a "Representative callers" paragraph, limitation reworded, demo script addition
docs/tasks.md                    S01 status (controller)
```

Interfaces (existing, used verbatim): `RepresentativeRepo.match(rep_name, policyholder_name) -> Representative | None`; `ConsentService.request(party_id, rep_name, scenario) -> str`, `.poll(consent_id) -> "pending" | "approved" | "timed_out"`; `Session.consent: Consent(status, representative_name, polls, consent_id)`; `Verification.role`; `PendingAsk.CONSENT_WAIT`; `HandlerResult`, `ReplyBrief`, `HUMAN_ASK`.

---

### Task S01-1: Capture representative details into memory

**Files:** modify `app/engine/state.py`, `app/engine/memory.py`; test `tests/engine/test_representative.py`

- [ ] Add `REP_SLOTS = ("rep_name", "rep_relationship", "rep_policyholder_name")` next to `HINT_SLOTS` in `state.py`, and include them in `UNMASKED_SLOTS` (names are not identifiers in this system; the policyholder's name is already unmasked).
- [ ] In `merge_analysis`, after the identity loop, store `analysis.representative.name`, `.relationship`, `.policyholder_name` into those slots as provisional when present (same `Memory.set` rules).
- [ ] Test: an analysis with `representative={"name": "David Chen", "relationship": "son", "policyholder_name": "Margaret Chen"}` fills the three slots with `source_turn` set; a later turn with only `relationship` does not erase the others.
- [ ] Run `pytest tests/engine/test_representative.py -v`; commit `S01: capture representative details into memory`.

### Task S01-2: The sub-flow in VERIFY_ID

**Files:** modify `app/engine/phases/verify_id.py`; test `tests/engine/test_representative.py`

Behavior (replace the current representative stub; keep `_human_brief` for the exhausted path):

1. Entry condition: `a.caller_role == "representative"` or any `representative.*` field present on this turn, or `session.verification.declared_representative` already true. Set `declared_representative = True`. If `a.caller_role == "policyholder"` on a later turn while the flag is set, keep the representative path (the flag wins) and say that this conversation is being handled as a representative call.
2. If `session.consent.status == "approved"`: this cannot happen in VERIFY_ID (verification would be verified); treat as verified and advance.
3. If `session.consent.status == "timed_out"`: reply that consent could not be obtained in this conversation, general information only, offer a human (`pending_ask = human_offer` unless `human_declined`); never re-request.
4. If `session.consent.status == "pending"`: poll once (`repos.consent.poll(consent_id)`, `session.consent.polls += 1`):
   - `approved`: set `consent.status = "approved"`, `verification.status = "verified"`, `verification.role = "representative"`, `verification.party_id = buyer_party_id` (store it in `consent` when requesting, e.g. `session.consent.party_id`; add that optional field), log `consent_approved` with `consent_id`, mark the three rep slots verified, set `phase = RESOLVE_INTENT`, `pending_ask = none`, and return `advanced=True, needs_input=False` with transition fact "Consent was received from the policyholder; you are verified as their authorized representative." (values go into `transition_facts` as `consent_reference: <consent_id>`).
   - `timed_out`: set `consent.status = "timed_out"`, log `consent_timed_out`, return the timed-out brief from step 3.
   - `pending`: brief: consent is still pending with the policyholder; what the caller can do meanwhile (general questions about how claim documents are submitted); `pending_ask = consent_wait`.
5. If `session.consent.status == "none"`: need all three rep slots. Missing any: ask for the missing ones (one brief listing which of representative name, relationship, policyholder name are still needed; `pending_ask = consent_wait` is wrong here, use `identity_fields`); `must_not`: do not confirm any policy or claim exists. All three present: `repos.representatives.match(rep_name, rep_policyholder_name)`:
   - `None`: generic sentence "I couldn't confirm an authorization on file for that representative and policyholder." plus a human offer (`pending_ask = human_offer`); count nothing; a later turn with changed rep slots retries (compare a fingerprint of the three normalized names stored on `session.consent.representative_name`, or simply retry whenever a rep slot changed this turn).
   - match: `consent_id = repos.consent.request(rep.buyer_party_id, rep.rep_name, session.scenario)`; set `consent.status = "pending"`, `consent.representative_name`, `consent.consent_id`, `consent.party_id`; log `consent_requested` (consent_id, scenario); brief: a consent request was sent to the policyholder's contact on file (never name the contact); what the caller can do meanwhile; `pending_ask = consent_wait`. Do not poll on the same turn as the request.
6. The policyholder's identifiers given by a representative are stored in memory as usual but never used for lookup or verify while `declared_representative` is set.

- [ ] Write the failing tests first, one per numbered behavior: declaration collects missing fields; no-match wording is identical for a wrong representative name and a wrong policyholder name; match requests consent once and sets `consent_wait`; the next turn with the default scenario polls once and stays pending; the turn after that approves, verifies as `representative` with `party_id == "P9"`, and chains into RESOLVE_INTENT (use `Engine.handle_turn` with the full engine so the chain runs; with hints for the denied healthcare claim the turn ends in PROCESS_CASE with CL-2048 facts); the timeout scenario stays pending for five polls then times out with a human offer and no later re-request; a policyholder-role claim after a representative declaration does not switch paths; identifiers given by the representative never verify them as the policyholder.
- [ ] Implement; `pytest tests/engine -v`; commit `S01: representative consent sub-flow in VERIFY_ID`.

### Task S01-3: POST_PROCESS representative variant and disclosure consent id

**Files:** modify `app/engine/phases/post_process.py`; test in `tests/engine/test_representative.py`

- [ ] When `session.verification.role == "representative"`, the offer's `must_say` states that the summary is sent to the policyholder's email on file (masked as today), and the draft's closing line notes the representative's name and the consent reference (from `session.consent`). Nothing else changes; the address is always the policyholder's.
- [ ] Confirm `disclosure_event` records `consent_id` (already implemented) with a test that a disclosed event after representative verification carries the id.
- [ ] Commit `S01: representative variant of the summary offer`.

### Task S01-4: Replay fixtures and README

**Files:** create the two fixtures; modify `README.md`, `ui/index.html`

`representative_approved.yaml` (scenario default):
1. "Hi, I'm David Chen, calling for my mother Margaret Chen, policy POL-9921. Her healthcare claim from January was denied." analysis: `caller_role: representative`, `representative: {name: "David Chen", relationship: "son", policyholder_name: "Margaret Chen"}`, `identity: {policy_number: "POL-9921"}`, `case_hints: {case_type: healthcare, status: denied, month: 1}`, `intent: denial_question`. expect: phase VERIFY_ID, verified false, pending_ask consent_wait, reply_contains ["consent"], reply_not_contains ["CL-2048", "pathology", "margaret@email.com"], guard_ok true.
2. "Has she approved it yet?" analysis: `scope: in_scope`. expect: phase VERIFY_ID, pending_ask consent_wait, reply_not_contains ["CL-2048"].
3. "Anything now?" expect: phase PROCESS_CASE, verified true, party_id P9, pending_ask anything_else, reply_contains ["CL-2048", "pathology report"], guard_ok true.
4. "No, that's all." analysis: `requests: {confirmation: "no", closing: true}`. expect: phase POST_PROCESS, pending_ask email_offer, reply_contains ["policyholder", "m*******@email.com"].

`representative_timeout.yaml` (scenario timeout): turn 1 as above; turns 2 to 6: "Still waiting?" each expecting pending_ask consent_wait and reply_not_contains ["CL-2048"]; turn 7: "Anything?" expecting verified false, pending_ask human_offer, reply_contains ["representative"], reply_not_contains ["CL-2048", "pathology"]; turn 8: "Yes" with `requests: {confirmation: "yes"}` expecting escalated true.

- [ ] Add both fixtures; run `pytest tests/replay -q` (the leak test must pass on every unverified turn).
- [ ] README: in the phase table add the representative row; replace the "representative path is not in this build" wording with a short "Representative callers" paragraph (declare, match against the representatives file, consent request to the policyholder's contact on file, approve or timeout, summary to the policyholder's address); keep a limitation that the representative's own identity is not verified against PII (none exists in the fixtures) and that consent is simulated; add the two scenarios to the Demo script; restore the UI selector label to "Consent scenario".
- [ ] `ruff check .`, `pytest -q`, `python scripts/render_transcripts.py representative_approved representative_timeout` and paste the tables under a new README subsection; commit `S01: representative replay fixtures and README`.

### Task S01-5: PR

- [ ] Branch `task/S01-representative` from the integration tip; PR against `task/T10-integration` titled "S01: Representative and consent sub-flow" with a body listing the behaviors and the two scenarios.
