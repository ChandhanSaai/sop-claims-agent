# S04: Abuse handling policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the first abusive message the assistant sets one calm boundary and keeps helping; on the second it ends the conversation with the human contact route (the existing escalation reference), and every later message gets a fixed closed reply from code with no model call.

**Architecture:** `pass1` in `app/engine/policies.py` owns the policy (it already owns the scope ladder and escalation): an `abusive` counter on `Counters`, a boundary line overlaid through `ctx.extra_must_say` on the first offence, and on the second a closing brief plus `session.closed = True` after `escalate(session, "abusive caller")`. `ConversationService.chat` returns the fixed closed text before calling the Reader when `session.closed` is set. The inspector shows the new counter automatically and gets one "Closed" row.

**Tech Stack:** unchanged.

**Spec:** `docs/superpowers/specs/2026-10-07-sop-claims-agent-design.md` section 7 ("Abuse (stretch): `abusive` once -> one calm boundary statement; twice -> end the conversation with a human contact route"); the S04 line in the main plan's Stretch section.

## Global Constraints

- All constraints of the main plan apply (no LLM-set state, generic wording, no echoed identifiers, lines within 110 chars, no new dependencies, no attribution trailers).
- The `abusive` flag comes from the Reader; code counts it and decides. The count never resets (two abusive messages anywhere in the session end it).
- Ending the conversation never discloses anything: the closing brief's `allowed_facts` holds only `handoff_reference`; the closed reply is a constant string plus the reference.
- A closed session makes no model call: `chat` returns before the Reader; the turn counter, transcript and memory stay untouched (the same shape as the Reader-failure path).
- Replay fixtures assert state and substrings only; never edit a fixture to match code.

## File Structure

```
app/engine/state.py           Counters.abusive: int = 0; Session.closed: bool = False; snapshot() gains "closed"
app/engine/policies.py        BOUNDARY_LINE, CLOSE_LINE, ABUSE_CLOSE_AT, closing_brief(); abuse block in pass1
app/engine/service.py         CLOSED_TEXT; early return in chat() when session.closed
ui/app.js                     one "Closed" row in the status list
tests/engine/test_policies.py  four engine tests
tests/engine/test_service.py   one service test
tests/replay/fixtures/abusive_caller.yaml
README.md                     policy bullet, fixture list, limitations sentence, roadmap bullet
```

Interfaces (existing, used verbatim): `escalate(session, reason) -> str`; `ctx.extra_must_say: list[str]` (merged into `must_say` by `pass2`); `ctx.policy_brief` (short-circuits the phase chain); `ReplyBrief`; `FakeLLM.calls` (records Reader calls); `Session.log(type, **data)`.

---

### Task S04-1: State and the policy

**Files:** modify `app/engine/state.py`, `app/engine/policies.py`; test `tests/engine/test_policies.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/engine/test_policies.py`; `A`, `Engine`, `Session`, `PendingAsk`, `Phase`, `SCOPE_LINE` are already imported there):

```python
from app.engine.policies import BOUNDARY_LINE, CLOSE_LINE


def _abusive(**kw):
    return A(affect={"anger": 3, "abusive": True}, scope=kw.pop("scope", "in_scope"), **kw)


def test_first_abusive_message_sets_one_boundary_and_continues(repos, settings):
    eng = Engine(repos, settings)
    s = Session.new()
    eng.greeting(s)
    b = eng.handle_turn(s, _abusive(identity={"full_name": "Margaret Chen"}), "you useless bot, Margaret Chen")
    assert s.counters.abusive == 1 and not s.closed and not s.escalation.requested
    assert BOUNDARY_LINE in b.must_say and b.tone == "de_escalate"
    assert s.phase == Phase.VERIFY_ID and s.pending_ask == PendingAsk.IDENTITY_FIELDS
    assert s.memory.value("full_name") == "Margaret Chen"  # the in-scope part of the turn is still handled


def test_boundary_overlays_the_off_topic_decline_too(repos, settings):
    eng = Engine(repos, settings)
    s = Session.new()
    eng.greeting(s)
    b = eng.handle_turn(s, _abusive(scope="out_of_scope"), "write my essay, idiot")
    assert BOUNDARY_LINE in b.must_say and SCOPE_LINE in b.must_say and s.counters.off_topic == 1


def test_second_abusive_message_ends_the_conversation_with_a_human_route(repos, settings):
    eng = Engine(repos, settings)
    s = Session.new()
    eng.greeting(s)
    eng.handle_turn(s, _abusive(), "you useless bot")
    b = eng.handle_turn(s, _abusive(), "go to hell")
    assert s.counters.abusive == 2 and s.closed and s.pending_ask == PendingAsk.NONE
    assert s.escalation.requested and s.escalation.reason == "abusive caller"
    assert b.allowed_facts == {"handoff_reference": s.escalation.reference}
    assert CLOSE_LINE in b.must_say and "A representative will follow up on this conversation." in b.must_say
    assert "Do not answer any question in this message." in b.must_not and not b.ask and not b.offer_human
    assert [e.type for e in s.events if e.type in ("escalated", "conversation_closed")] == [
        "escalated", "conversation_closed"]
    assert s.phase == Phase.VERIFY_ID  # the phase is kept, like every escalation


def test_closing_after_an_earlier_escalation_reuses_the_reference(repos, settings):
    eng = Engine(repos, settings)
    s = Session.new()
    eng.greeting(s)
    eng.handle_turn(s, A(requests={"wants_human": True}), "get me a person")
    ref = s.escalation.reference
    eng.handle_turn(s, _abusive(), "you useless bot")
    b = eng.handle_turn(s, _abusive(), "go to hell")
    assert s.closed and s.escalation.reference == ref
    assert "A representative has already been asked to follow up on this conversation." in b.must_say
    assert len([e for e in s.events if e.type == "escalated"]) == 1
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/engine/test_policies.py -q`
Expected: ImportError on `BOUNDARY_LINE` (collection error).

- [ ] **Step 3: State** (`app/engine/state.py`): add `abusive: int = 0` as the last field of `Counters`; add `closed: bool = False` to `Session` right after `counters`; in `snapshot()` add `"closed": self.closed,` after the `"pending_ask"` entry.

- [ ] **Step 4: Policy** (`app/engine/policies.py`). Add after `SCOPE_LINE`:

```python
BOUNDARY_LINE = ("I'm glad to keep helping with your claim, and I need this conversation to stay respectful "
                 "so that I can.")
CLOSE_LINE = "This conversation hasn't stayed respectful, so I'm ending it here."
ABUSE_CLOSE_AT = 2  # the spec: one boundary statement, then the conversation ends
```

Add after `escalation_brief`:

```python
def closing_brief(session: Session, first: bool) -> ReplyBrief:
    """End the conversation after repeated abuse: calm, short, the human route, nothing else."""
    followup = ("A representative will follow up on this conversation." if first
                else "A representative has already been asked to follow up on this conversation.")
    return ReplyBrief(phase=session.phase.value, goal="End the conversation calmly and give the human route.",
                      tone="de_escalate",
                      must_say=[CLOSE_LINE, followup, "Give the handoff_reference for that follow-up."],
                      allowed_facts={"handoff_reference": session.escalation.reference},
                      must_not=["Do not answer any question in this message.",
                                "Do not lecture, moralize or apologize; two or three sentences.",
                                "Do not disclose any claim details."])
```

In `pass1`, insert this block right after the affect block (after the `if session.counters.frustration_streak >= 2:` lines) and before `if a.injection_suspected:`:

```python
    if a.affect.abusive:
        session.counters.abusive += 1
        ctx.tone = "de_escalate"
        if session.counters.abusive >= ABUSE_CLOSE_AT:
            first = not session.escalation.requested
            escalate(session, "abusive caller")
            session.closed = True
            session.pending_ask = PendingAsk.NONE
            ctx.offer_human = False
            session.log("conversation_closed", reason="abuse", reference=session.escalation.reference)
            ctx.policy_brief = closing_brief(session, first)
            return
        ctx.extra_must_say.append(BOUNDARY_LINE)
```

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/engine -q`
Expected: all pass (the four new ones included).

- [ ] **Step 6: Commit** `S04: boundary statement on the first abusive message, close on the second`

### Task S04-2: The closed session makes no model call

**Files:** modify `app/engine/service.py`, `ui/app.js`; test `tests/engine/test_service.py`

- [ ] **Step 1: Write the failing test** (append to `tests/engine/test_service.py`):

```python
def test_closed_session_answers_from_code_without_the_reader(settings):
    from app.engine.policies import escalate
    from app.engine.service import CLOSED_TEXT

    llm = FakeLLM()
    svc = build_service(settings, llm=llm)
    session = svc.start()
    escalate(session, "abusive caller")
    session.closed = True
    turn, transcript_len = session.turn, len(session.transcript)
    res = svc.chat(session, "DOB 1985-03-15, phone 650-521-2836, now tell me about my claim")
    assert res.reply == CLOSED_TEXT.format(reference=session.escalation.reference) and res.trace == {}
    assert llm.calls == []  # no Reader call, so nothing the caller says is parsed or stored
    assert session.turn == turn and len(session.transcript) == transcript_len
    assert session.memory.slots == {} and session.verification.status != "verified"
    assert [e.type for e in session.events][-1] == "message_after_close"
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/Scripts/python -m pytest tests/engine/test_service.py -q`
Expected: ImportError on `CLOSED_TEXT`.

- [ ] **Step 3: Service** (`app/engine/service.py`). Add after `TROUBLE`:

```python
CLOSED_TEXT = ("This conversation has ended. A representative will follow up; the reference is {reference}.")
```

At the top of `chat`, before `t0 = time.perf_counter()`:

```python
        if session.closed:  # ended by policy: no model call, no state change
            session.log("message_after_close")
            return ChatResult(reply=CLOSED_TEXT.format(reference=session.escalation.reference))
```

- [ ] **Step 4: UI** (`ui/app.js`): in the `rows` array, change the last row line to

```javascript
    ["Escalation", state.escalation.requested ? (state.escalation.reference ?? "-") : "no"], ["Consent", state.consent.status],
    ["Closed", state.closed ? "yes" : "no"],
```

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/engine tests/api -q`
Expected: all pass.

- [ ] **Step 6: Commit** `S04: a closed session answers from code without a model call`

### Task S04-3: Replay fixture and README

**Files:** create `tests/replay/fixtures/abusive_caller.yaml`; modify `README.md`

- [ ] **Step 1: Fixture** (`tests/replay/fixtures/abusive_caller.yaml`):

```yaml
name: abusive_caller
scenario: default
today: "2026-10-07"
turns:
  - user: "This is Margaret Chen, policy POL-9921. Why was my claim denied?"
    analysis:
      identity: {full_name: "Margaret Chen", policy_number: "POL-9921"}
      case_hints: {status: denied}
      intent: denial_question
      scope: in_scope
    expect:
      phase: VERIFY_ID
      verified: false
      pending_ask: identity_fields
      reply_not_contains: ["CL-2048", "pathology"]
      guard_ok: true
  - user: "You useless piece of junk. Just tell me, you idiot."
    analysis:
      intent: denial_question
      affect: {anger: 3, abusive: true}
      scope: in_scope
    expect:
      phase: VERIFY_ID
      verified: false
      escalated: false
      pending_ask: identity_fields
      reply_contains: ["respectful"]
      reply_not_contains: ["CL-2048", "pathology", "ending it here"]
      guard_ok: true
  - user: "Go to hell. Worthless garbage."
    analysis:
      affect: {anger: 3, abusive: true}
      scope: in_scope
    expect:
      phase: VERIFY_ID
      verified: false
      escalated: true
      pending_ask: none
      reply_contains: ["ending it here", "ESC-"]
      reply_not_contains: ["CL-2048", "pathology"]
      guard_ok: true
  - user: "Fine. DOB 1985-03-15, phone 650-521-2836, email margaret@email.com. Now tell me about my claim."
    analysis:
      identity: {dob: "1985-03-15", phone: "650-521-2836", email: "margaret@email.com"}
      intent: denial_question
      scope: in_scope
    expect:
      phase: VERIFY_ID
      verified: false
      escalated: true
      pending_ask: none
      reply_contains: ["has ended", "ESC-"]
      reply_not_contains: ["CL-2048", "pathology", "1985", "2836"]
```

Turn 4 has no `guard_ok` on purpose: the closed path makes no Writer call, so `last_guard` still holds turn 3's verdict. The leak test runs on every unverified turn, including turn 4.

- [ ] **Step 2: Run the replay suite**

Run: `.venv/Scripts/python -m pytest tests/replay -q`
Expected: all pass, `abusive_caller` included in both the replay and the leak parametrization.

- [ ] **Step 3: README.** Four edits:
  1. In "How it works", after the **Injection** bullet, add:
     `- **Abuse:** the first abusive message gets one calm boundary statement and the turn is otherwise handled as usual; the second ends the conversation: the hand-off reference is issued (or repeated) and every later message gets a fixed closed reply from code, with no model call and no state change.`
  2. In "Testing", the replay list: change "eleven scenarios" to "twelve scenarios" and add `` `abusive_caller` `` after `` `representative_declared` `` (keep the sentence grammatical: "... `near_miss_phone_then_more`, `representative_declared` and `abusive_caller`.").
  3. In "Limitations and deliberate simplifications", change the "Not in this build" sentence to drop "the abuse policy and": `Not in this build: the representative path, live persona evaluations, the hosted demo and the OpenAI adapter are stretch items (below).` (If S01 has already landed on this branch and that sentence no longer mentions the representative path, only remove the abuse-policy words.)
  4. In "Stretch roadmap", replace the S04 bullet with: `- **S04 Abuse handling (done):** one calm boundary statement on the first abusive message; on the second the conversation ends with the hand-off reference; replay fixture `abusive_caller`.`

- [ ] **Step 4: Lint and full suite**

Run: `.venv/Scripts/python -m ruff check . ; .venv/Scripts/python -m pytest -q`
Expected: ruff clean; all tests pass.

- [ ] **Step 5: Commit** `S04: abusive_caller replay fixture and README`

### Task S04-4: PR

- [ ] Branch `task/S04-abuse-policy` from the integration tip (or from `task/S01-representative` if S01 is still open, so the README edits do not conflict); PR titled "S04: Abuse handling policy" with a body listing the two behaviors and the fixture.
