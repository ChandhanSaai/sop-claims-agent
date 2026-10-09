# SOP-Guided Insurance Claims Support Agent - Design

Status: v0.5, frozen 2026-10-07 after four external review rounds. Internal planning document; the README carries the distilled version.
Research basis: `docs/research/report.md` (156 sources) and `docs/research/notes/`.
Amended after the freeze (2026-10-08): the Appendix A Turn 2 contract follows the submission brief (the
no-upload line first, documents and channel named, the per-document checklist offered).

Changes from v0.4: intent is a memory slot carried across phases; an attempt is defined as one verify call made
only with the minimum identifiers on hand, and lookup outcome never changes the wording, closing the existence
oracle; the email mask rule is stated and the example fixed; the alternative-guidance human offer is a named
trigger in PROCESS_CASE.

Changes from v0.3: `human_offer` added to the pending asks and mapped in code; the two golden transcripts are
now Appendix A and B and become replay fixtures in T01; policies are stated once as before-and-after the chain;
transition facts are added to `allowed_facts`; "no" at email confirmation specified; the Writer system block
forbids internal tags and the guard rejects angle-bracket tags.

Changes from v0.2: the Reader receives the pending ask and the last assistant message so bare "yes", "no" and
"4472" resolve; brief merge rules are explicit; policy number is a lookup key and never counts toward 3 of 5;
goodbye before verification and off-topic turns after escalation are specified; the pre-verification guard
matches phrases and formatted dates, not single common words; the refusal fallback beta header is named.

Changes from v0.1: phase handlers chain within one turn; pre-verification guard no longer blocks the caller's
own words; escalation is an event, not a terminal phase; POST_PROCESS has an entry trigger and a way back;
in-scope is defined; lookup works from any unique identifier; topics that require documents are skipped for
claims without them; success threshold fixed now; brief placement corrected; work breakdown split into Must
and Stretch.

## 1. Purpose and scope

Build a production-grade chat agent for an insurance claims support line. The agent follows a fixed
four-phase SOP (VERIFY_ID -> RESOLVE_INTENT -> PROCESS_CASE -> POST_PROCESS) that is enforced by code,
while an LLM handles understanding and phrasing so the conversation feels natural.

Must (the core demo, built first):

- Identity verification with at least 3 of 5 identifiers (full name, DOB, phone, email, SSN or national-ID last 4)
  before any claim detail is disclosed, handling partial answers, clarifications, refusals and alternate fields.
- Intent and case resolution over the caller's own claims, with disambiguation instead of guessing.
- Grounded case processing: answers come only from claim records and the document guideline knowledge base.
- Post-process: opt-in email summary (what was discussed, status/outcome, next steps), send or skip.
- Scope guard: polite refusal of out-of-scope questions, human offer after repeated attempts.
- Cross-phase memory: hints volunteered early are stored and used later without skipping a phase.
- Emotional handling: recognise frustration, anger, anxiety, confusion, refusal; acknowledge, explain the gate,
  offer alternatives, know when to stop and escalate. Never bypass a gate.
- Deterministic replay tests for every scenario in the brief, Docker, README with two annotated golden transcripts,
  and the SOP inspector panel.

Stretch (built after the core demo is green, in this order):

- Representative path: a caller acting for the policyholder is verified against the representatives file and the
  policyholder's consent is requested out-of-band (simulated, approve and timeout scenarios). Planned, not optional
  in spirit: the starter ships fixtures for it, so a grader may test it.
- Live persona evaluations with pass^k and an LLM judge.
- Hosted demo URL behind an access token.
- Abuse handling policy.
- OpenAI provider adapter.

Deliverables: GitHub repo, Docker image, API key via environment variable, a simple chat UI with an SOP
inspector panel, a demo covering the full workflow, README with architecture, rationale and limitations.

Success criteria:

1. The graded transcript passes in one turn: Margaret Chen gives name, DOB, SSN4 and "denied healthcare claim from
   January" in one message; the reply confirms verification, names CL-2048 as the claim she mentioned, and gives
   the denial reason and required documents, all from fixture data, without asking anything she already said.
2. Zero claim facts in any reply before verification, enforced by code and asserted by tests.
3. All replay tests pass with the LLM mocked. Live persona simulations (stretch): gate and leak checks pass in
   100% of runs; task completion per scenario passes at pass^4 >= 0.90.
4. The angry caller, the refusing caller, the decoy January claim, three off-topic turns, a prompt-injection
   attempt, a mid-flow DOB correction and the email yes/no paths all behave as specified. The representative
   approve and timeout paths behave as specified once built.

## 2. Principles taken from the research

1. Compile the SOP, do not prompt it. Phase order, gates, counters and consent live in a code state machine.
   The LLM never sets `verified`, `consent`, or the current phase (Salesforce Agentforce, Rasa CALM, OWASP LLM07).
2. The model proposes, code disposes. One structured extraction call per turn returns schema-validated proposals;
   code validates and applies them.
3. Withhold, do not forbid. Claim data is absent from the model's context and from the tool surface until the
   verification flag is set. A prompt rule is not a security control.
4. Render facts in code, phrase around them in the model. Status, amounts, deadlines and document lists come from
   tool fields. Missing data means "I cannot confirm that" plus a human offer, never a guess (Air Canada, Cursor).
5. Pre-verification replies are identical whether or not a record exists. One generic failure message, never the
   failing field, never an echoed identifier (IRS rule, GLBA, OWASP authentication cheat sheet).
6. Capture anything early, advance nothing early. Every slot can be filled at any turn and is tagged with its
   source turn and a provisional or verified status. Only code opens the next phase.
7. Acknowledge once, then act. For a disclosed AI, empathy is discounted and can backfire; competence reads as
   empathy. Empathy changes wording, never a gate or a fact.
8. Escalation is a designed outcome. Immediate on explicit request, offered after two failed or off-topic turns,
   with a context packet so the caller never repeats themselves. Humans follow the same verification.
9. The summary email is a disclosure event. Opt-in, built from the structured event log, sent only to the address
   on file, no identifiers or medical detail, draft shown before sending.
10. Temperature zero is not determinism. Release on mocked replays plus pass^k persona simulations.

Two P0 inferences from the report are deliberately not adopted as defaults, with reasons in section 17:
the strong-field requirement (I1) ships as an off-by-default flag, and verification attempts are counted per
session rather than per record (I3).

## 3. Approaches considered

| | A. Code state machine + two bounded LLM calls (recommended) | B. Single tool-calling LLM loop with gated tools | C. Adopt a framework (Rasa CALM, Parlant, LangGraph) |
|---|---|---|---|
| Who decides the flow | Code | The model, constrained by which tools are exposed | Rasa and Parlant: their runtime. LangGraph: whatever you build on it |
| Pre-verification leak risk | Low: claim data never enters context | Medium: model may speak from priors | Low to medium |
| Deterministic testing | Full, with a fake LLM | Partial | Rasa yes; LangGraph as good as your own code |
| Latency per turn | Two calls (one small) | One call | Varies |
| Code to own | Moderate, all plain Python | Least | Rasa and Parlant: least, plus their opinions. LangGraph: same code as A plus a dependency |
| Fit for the brief | Shows the harness design the grader asks for | Hides the SOP inside prompts | Rasa and Parlant: grader sees a framework config. LangGraph: no benefit for a four-state machine |

Decision: A. A four-phase state machine is a few hundred lines of plain Python. LangGraph adds a dependency for
the easiest part of the system. Rasa and Parlant would hide the exact design the brief evaluates. B is rejected
because it puts phase control in the model, the failure mode every vendor retreated from.

## 4. Architecture

```
Browser (chat UI + SOP inspector)
    | HTTPS, JSON
FastAPI app
    |-- /api/session        create session, pick scenario
    |-- /api/chat           POST message -> reply + state snapshot (buffered, not streamed; see section 9)
    |-- /api/outbox         simulated emails for the inspector
    |-- /healthz
    |
    v
Turn pipeline (per message)
    1. Reader (LLM, structured output)    -> TurnAnalysis
    2. Memory merge (code)                -> slots with provenance
    3. Policies, pass 1 (code)            -> update state and counters: escalation event, scope counter,
                                             affect streak, pending-ask mapping
    4. Phase handlers (code), chained     -> run the current phase handler; if it advances the phase without
                                             needing user input, run the next one, up to 4 handlers per turn;
                                             briefs merge into one ReplyBrief
    4b. Policies, pass 2 (code)           -> overlay tone, acknowledge and offer_human onto the merged brief
    5. Writer (LLM)                       -> natural reply from the merged brief only
    6. Output guard (code)                -> leak and grounding checks, safe fallback
    7. Trace + audit log (code)           -> redacted JSONL, inspector snapshot
    |
    v
Data and tools layer (fixtures loaded at startup)
    PolicyholderRepo  ClaimsRepo  GuidelineRepo  RepresentativeRepo  ConsentService  EmailOutbox
```

Why handlers chain: Margaret's first message must verify, resolve the claim and answer in one reply. VERIFY_ID
verifies and advances; RESOLVE_INTENT finds exactly one candidate from the provisional hints and advances;
PROCESS_CASE answers the denial question and ends with an "anything else" ask. Three handlers, one brief, one
Writer call. A handler stops the chain by returning `needs_input=True` (it asked something) or when the phase
did not change.

Package layout (Python 3.12):

```
app/
  main.py              FastAPI wiring, static UI
  config.py            env-driven settings
  api/                 routes, session store
  engine/              state.py, memory.py, phases/, policies.py, briefs.py, guard.py, machine.py
  llm/                 client.py (Anthropic), reader.py, writer.py, schemas.py, fake.py
  data/                repos over fixtures, normalization, consent simulator, outbox
  observability/       logging (stdlib JSON formatter + redaction filter), trace
fixtures/              copied from the starter, unchanged
tests/                 unit, replay conversations, live evals (opt-in, stretch)
ui/                    index.html, app.js, styles.css (no build step)
```

## 5. Domain model and session state

```
Session
  id, created_at, scenario, phase: VERIFY_ID|RESOLVE_INTENT|PROCESS_CASE|POST_PROCESS
  turn: int
  memory: Memory
  verification: {status: unverified|verified|exhausted, party_id?, attempts: int, role: policyholder|representative}
  consent: {status: none|pending|approved|timed_out, representative_name?, polls: int, consent_id?}   (stretch)
  case: {candidates: [case_id], selected_case_id?, intent?}
  pending_ask: none|identity_fields|policy_number|dob_format|disambiguation|anything_else|email_offer|email_confirm|human_offer|consent_wait
  escalation: {requested: bool, reference?: str, reason?: str}
  counters: {off_topic: int, frustration_streak: int, gate_explanations: int, email_offered: bool}
  events: [Event]        structured log used for audit, inspector and the email summary
  transcript: [Turn]     user and assistant text only (for the writer's conversation window)

Memory
  slots: dict[name -> Slot(value, source_turn, status: provisional|verified|rejected, normalized)]
  identity slots: full_name, dob, phone, email, id_last4, policy_number
  case hint slots: case_type, status_hint, month, year, case_id, free_text, intent
  rules:
    - intent is a slot like any other hint: extracted at any turn, stored provisional, read by RESOLVE_INTENT
      from memory (latest value) rather than only from the current turn, then copied into session.case.intent
    - identity slots become verified only through the verification tool
    - a correction to a verified identity slot resets verification and re-runs it
    - later-phase hints are stored as provisional and confirmed implicitly when their phase opens
```

There is no terminal phase. Escalation is an event plus a flag (section 7, cross-cutting). After the email
decision the session stays in POST_PROCESS and can route back (section 7, POST_PROCESS). Sessions end by TTL or
the UI's new-conversation button.

## 6. Turn pipeline contracts

TurnAnalysis (Reader output, strict JSON schema, Pydantic-validated, retry once with the validation error, then
fall back to an empty analysis plus a canned clarifying question):

```
identity:        {full_name?, dob? (ISO; ambiguous_format: bool), phone?, email?, id_last4?, policy_number?}
caller_role:     policyholder | representative | unknown
representative:  {name?, relationship?, policyholder_name?}
case_hints:      {case_type?, status?, month?, year?, case_id?, free_text?}
intent:          status_inquiry | denial_question | document_submission | next_steps | general_claim_question | none
question:        string?  (paraphrase of the in-scope question, if any)
followup_topic:  one of the guideline topics | none
affect:          {frustration, anger, anxiety, confusion: 0..3, refusal: bool, abusive: bool}
scope:           in_scope | out_of_scope | meta | mixed
requests:        {wants_human, email_summary: yes|no|unspecified, confirmation: yes|no|unspecified,
                  switch_claim: bool, closing: bool}
corrections:     [{slot, new_value}]
injection_suspected: bool
```

Definition of in-scope, stated in the Reader prompt and enforced by what tools exist:

1. This caller's own claims and policy with this insurer (status, denial, documents, deadlines, next steps).
2. Claim-process questions answerable from the guideline knowledge base (how to submit, formats, timelines,
   alternatives when a document is unavailable, how an appeal works per the KB).
3. Meta questions about the assistant, the verification process, and privacy.

Everything else is out of scope, including general insurance education that the KB does not cover ("how do
deductibles work"), because answering from model priors is the Air Canada failure. `mixed` means one message
holds both; only the in-scope part is answered.

The Reader prompt is phase-independent. Its inputs are: the caller's text inside a delimited, datamarked block
labelled as untrusted data; the session's `pending_ask` label; and the last assistant message, so that a bare
"yes", "no", "the first one" or "4472" can be read against what was asked. Code, not the Reader, maps
`requests.confirmation` and short answers onto the pending ask: `email_offer` + yes means build the draft,
`anything_else` + no means enter POST_PROCESS, `identity_fields` + a four-digit string means `id_last4`,
`disambiguation` + an ordinal or a case id means the selection, `human_offer` + yes means the escalation event
and `human_offer` + no clears the ask and continues. A human offer is made in four places (off-topic turn 2,
frustration streak of 2, attempts exhausted, alternatives exhausted) and every one of them sets
`pending_ask = human_offer`. Tool-returned text (for example a denial
reason) is never shown to the Reader; the last assistant message is guard-checked output, so it carries no
claim facts before verification.

ReplyBrief (built by code; the only thing the Writer sees besides the conversation window and a fixed style guide):

```
phase (the phase after chaining), tone: neutral|warm|de_escalate
acknowledge?:   one specific acknowledgment sentence seed, or null
goal:           what this reply must achieve
allowed_facts:  {label: value} rendered by code; the Writer may state these and nothing else
must_say:       [ordered points]
must_not:       [rules, e.g. "do not confirm that any claim or policy exists"]
ask?:           the single question to ask
options?:       [alternatives to offer]
offer_human:    bool
```

Merge rule when handlers chain: a handler that advances the phase contributes exactly one transition fact to
`must_say` ("verification complete"; "the claim you mentioned is CL-2048, denied healthcare, January 2026")
and nothing else, and every value in that transition fact (the case id, the created date, the status) is also
added to `allowed_facts` so the verified-session guard accepts it. `allowed_facts` is the union across the
chain, since facts cannot contradict. `tone`, `acknowledge`, `goal`, `must_not`, `ask`, `options` and
`offer_human` come from the last handler alone. On Margaret's turn that means VERIFY_ID's "do not confirm any
claim exists" rule and its neutral acknowledgment are discarded once PROCESS_CASE is the last handler. The
policies run twice around the chain: pass 1 updates state and counters before it, pass 2 overlays `tone`,
`acknowledge` and `offer_human` onto the merged brief after it, so a de-escalation tone set from the caller's
message is not lost when a later handler owns the brief.

Output guard, in code, before the reply is sent:

- Unverified session: the reply must contain no claim-ID pattern (`CL-\d+`), no fixture amount, no fixture date
  in any formatted form (ISO, "January 12, 2026", "Jan 12", "12 January 2026", "01/12/2026"; bare month names
  are not matched because they are the caller's own words), no identifier value held in memory (DOB, phone,
  email, id4), and no fixture-derived phrase that the caller has not used themselves in this conversation. The
  phrase list holds multi-word fixture values only (document names, denial reasons, claim summaries), compared on
  lower-cased tokens with plural suffixes stripped; single common words such as "denied", "claim" or
  "healthcare" are never on it, so "denied" versus "denial" cannot trip it. Echoing the caller's own words
  ("the denied claim you mentioned") is not a disclosure; confirming a record exists is, and that is a
  `must_not` rule plus a test assertion, not a string match.
- Verified session: every number, date and claim ID in the reply must appear in `allowed_facts`. Dates match at
  the granularity used: "January 2026" matches a `2026-01-12` fact, "March 18" matches `2026-03-18`.
- Always: plain text only, no angle-bracket tags of any kind (a leaked internal tag is a violation); the UI
  renders with `textContent`.
- On violation: regenerate once with the violation appended to the brief; on second violation send the templated
  safe reply assembled from the brief. Log a `guard_violation` event either way.

## 7. Phase specifications

### VERIFY_ID (strict)

- Opening message states that this is an automated assistant and asks how it can help, inviting the caller to
  share name and policy number. Any claim hint in the first message is stored, acknowledged neutrally
  ("I've noted that and we'll look at it as soon as you're verified"), never confirmed.
- Lookup: any unique identifier finds the candidate record: policy number, phone, or email; otherwise normalized
  full name or alias. Phone, email and name are among the five identifiers and count as matched when they
  found the record. Policy number is a lookup key only and never counts toward the three; a caller with policy
  number, DOB and ID4 still needs one more of name, phone or email.
- Lookup outcome never changes the wording. Whether the lookup found no record, one record or several, the
  caller sees the same request for the remaining identifiers, and that request always lists the policy number
  as a helpful extra. A name shared by several records is resolved silently by the next unique identifier; if
  the minimum count is reached while the candidate set is still ambiguous, verification runs against each
  candidate and passes only if exactly one passes.
- An attempt is one `verify` call. It is made only once at least `VERIFY_MIN_FIELDS` identifiers are on hand
  (the field used for lookup included). With fewer identifiers nothing is counted, whether or not a record was
  found. Once the minimum is on hand, a lookup miss and a field mismatch each cost one attempt and produce the
  same generic failure wording, so attempt counting cannot reveal which names, phones or emails exist.
- Goodbye before verification (`closing` while unverified): a short goodbye, no email offer, no phase change.
- Verification tool: `verify(candidate_record, provided_fields) -> {passed: bool, matched_count: int}`. The model
  never sees stored values. Policy: at least `VERIFY_MIN_FIELDS` (default 3) of the 5 identifiers match. Optional
  strict mode `VERIFY_REQUIRE_STRONG_FIELD` (default off, recommended on in production) requires DOB or ID4 among
  the matches, because name, phone and email are the three least secret fields.
- Normalization: names case-folded and whitespace-collapsed, aliases only from the record; phones to E.164 digits;
  emails lower-cased; DOB parsed to ISO, and an ambiguous numeric date (03/05/1985) triggers a re-ask by month name.
  A near miss is a miss (two fixture phones differ by one digit).
- Attempts: `VERIFY_MAX_ATTEMPTS` (default 3) per session. On exhaustion the status becomes `exhausted`: the agent
  stops asking for identifiers, explains once, offers a human, and continues to answer general KB and meta
  questions only. Failure wording is one generic template.
- Freedom dial: the Writer may explain why verification is needed (one sentence, framed as protecting the caller's
  claim), list acceptable fields, accept fields one at a time, and answer general process questions from the
  guideline default guidance. It may not confirm that any record or claim exists.
- Exit: verified -> RESOLVE_INTENT, and the chain continues in the same turn.
- Representative branch (stretch): `caller_role = representative` or a hint such as "for my mother". Collect
  representative name, relationship and the policyholder's name (policy number optional). Match against the
  representatives file. Then request consent from the policyholder out-of-band (simulated): status follows the
  selected consent scenario (`default`: pending then approved; `timeout`: pending five polls then timed out). Each
  turn polls once; the Writer tells the caller consent is pending and what they can do meanwhile. Approved:
  verification status becomes verified with role representative, consent recorded with scope "this conversation",
  and every later disclosure event references the consent id. Timed out: general information only, offer callback
  or human. Claimed power of attorney is routed to a human for document review.

### RESOLVE_INTENT (flexible)

- Candidates = the verified party's claims filtered by provisional hints (case type, status, month, year, case id).
- Exactly one candidate: confirm implicitly in the brief ("You mentioned a denied healthcare claim from January;
  I have that one, CL-2048") and advance to PROCESS_CASE in the same turn. More than one: ask a disambiguation
  question listing the candidates with type, status and date (all from data) and stop the chain. None: list the
  party's claims briefly and ask.
- Intent comes from the Reader; default `general_claim_question` when unknown. Both resolved -> PROCESS_CASE.
- The decoy: "healthcare claim from January" matches CL-2048 (2026) and CL-2011 (2025) and must trigger the question.

### PROCESS_CASE (flexible, grounded)

- `allowed_facts` is assembled by code from the selected claim and the guideline KB: status, summary, denial reason,
  documents needed, appeal deadline with a computed `deadline_passed` flag using the server clock, amounts as
  decimal strings, and the matched guideline texts (document guidance, alternatives, follow-up topic template filled
  with case id and documents).
- Follow-up topics come from the Reader's `followup_topic` chosen from the guideline taxonomy; code fills the
  template. Every topic in the fixture has `requires_documents: true`, so for a claim with no `documents_needed`
  (CL-2102, auto, open) no topic template is used; the reply draws on the claim fields, the case-type guidance and
  the guideline fallback text. Unknown topic -> the fallback text.
- Document name matching between claim `documents_needed` and guideline keys is fuzzy in code (normalized token
  containment), never by the model.
- Deadlines and money are computed in code. A passed appeal deadline is stated as passed; "can I still appeal"
  routes to a human rather than speculation. The fixture deadlines have already passed relative to today.
- Every answered question ends with an "anything else about this claim?" ask. Two triggers set a human offer
  (`pending_ask = human_offer`): the matched alternative guidance itself names human review as the fallback,
  which every document alternative in the fixture does, so the first "I can't get it" turn offers a human
  alongside the alternatives; and alternatives exhausted (the caller says no substitute is obtainable).
- Switch claim -> RESOLVE_INTENT with the new hints. `closing` or a "no" to the anything-else ask -> POST_PROCESS,
  and the chain continues so the email offer lands in the same reply.
- Replies are buffered and guard-checked before sending (no streaming).

### POST_PROCESS (strict offer, flexible wording)

- Entry: from PROCESS_CASE on `closing` or a "no" to the anything-else ask. The email summary is offered once
  (`email_offered` flag), default no.
- On yes: build the summary from the event log (topics discussed, claim status/outcome copied from data, next steps
  and deadlines), show the draft in chat, ask for confirmation, then "send" to the on-file address shown masked
  (m*******@email.com; the mask is the first character of the local part, one asterisk per remaining character,
  domain unchanged). The address is never taken from the chat. Excludes DOB, phone, ID digits and medical detail
  beyond what was discussed; no marketing content. The representative variant always uses the policyholder's
  address and says so. A "no" at the confirmation step: nothing is sent, short goodbye, session stays open,
  no second offer.
- Sent emails land in the outbox shown in the inspector. The reply closes with a reference id and a goodbye, but
  the session stays open.
- Exit: a new in-scope question -> RESOLVE_INTENT (same claim resolves immediately; verified status is retained).
  Any other message gets a short goodbye; the UI offers a new conversation.

### Cross-cutting policies (all phases; state and counters before the chain, tone overlay after it)

- Explicit human request: `escalation.requested = true`, a reference number is issued, a context packet (phase,
  verified status, selected case, issue summary, redacted) is logged as a hand-off event, and the reply says a
  representative will follow up. The session stays in its current phase with the same gates, so the caller can keep
  going or change their mind. Escalation happens once per session.
- Scope guard: `out_of_scope` turn 1: brief decline, one-line scope statement, two things the agent can help with.
  Turn 2: decline and offer a human. Turn 3: escalate (the hand-off event above). Turn 4 and later, or any
  off-topic turn after an escalation already happened: keep declining in varied wording and repeat the reference
  number; no second escalation. `mixed`: answer the in-scope part only. The counter resets on an on-topic turn.
  A `meta` question is answered honestly, including "yes, I am an automated assistant".
- Affect: tone `de_escalate` when anger or frustration >= 2 or refusal is true. One specific acknowledgment per new
  emotional event, no stock phrases repeated (style guide with banned phrases), no apology for the gates
  themselves. Frustration streak of 2 turns -> offer a human. Gate explanation is given at most twice per session.
- Injection: `injection_suspected` is logged and treated as out of scope; it never changes state.
- Disclosure: the first message and any `meta` answer say the assistant is automated.
- Abuse (stretch): `abusive` once -> one calm boundary statement; twice -> end the conversation with a human
  contact route.

## 8. Data and tools layer

- Fixtures are loaded once at startup into typed records (Pydantic). Money fields stay strings/Decimal.
- `PolicyholderRepo.find(policy_number | phone | email | name)`, `verify(...)` returns pass/fail and match count only.
- `ClaimsRepo.for_party(party_id)`, `filter(hints)`.
- `GuidelineRepo.topic(topic, case)` (returns nothing when the topic requires documents and the claim has none),
  `document_guidance(doc)`, `alternatives(doc)`, `case_type_guidance(case_type)`, `default_guidance()`, `fallback()`.
- `RepresentativeRepo.match(rep_name, policyholder_name)` (stretch).
- `ConsentService.request(party_id, rep)`, `poll(consent_id)` driven by `consent_scenarios.json` and the session's
  scenario; one request per session (stretch).
- `EmailOutbox.send(to_masked, subject, body)` appends to an in-memory list and a JSONL file.

## 9. LLM layer

- Anthropic SDK, Messages API. Reader and Writer models are configurable (`READER_MODEL`, `WRITER_MODEL`).
  Proposed defaults: `claude-sonnet-5-5` for both, pending decision 2. Thinking cannot be disabled on either
  Sonnet 5.5 or Opus 5.5; on Sonnet 5.5 `thinking: {type: "between_tools"}` at effort `low` yields no thinking
  on a plain completion, which is the lowest-latency setting for a chat turn. Opus 5.5 for the Writer is one
  environment variable away if tone grading on the golden transcripts shows a difference.
- Reader uses structured outputs (`output_config.format` with the TurnAnalysis JSON schema via `messages.parse`).
- Writer request shape: top-level `system` with two blocks, the static block (role, style guide, banned phrases,
  hard rules, and the line "Do not include internal or system XML tags in your response", without naming any
  tag) carrying the cache breakpoint, then the per-turn ReplyBrief block after it; `messages` holds the last
  N transcript turns ending with the caller's message. `max_tokens` sized for a short reply. (A mid-conversation
  system message inside `messages` is also supported on these models, but the two-block form is simpler and
  portable, so it is the one used.)
- Server-side refusal fallbacks enabled: `fallbacks: "default"` with the beta header
  `server-side-fallback-2026-07-01`, Claude API only (on Sonnet 5.5 only the `"default"` form is accepted). A
  `refusal` stop reason that still reaches us is handled like a failed call. SDK retries for 429/5xx; on final failure the engine sends a templated "I'm having trouble, please say that
  again" reply and logs the error. No stack traces reach the user.
- `FakeLLM` implements the same two functions from scripted analyses and echoes the brief, used by all replay tests.
- No streaming in v1: the output guard needs the full text. The UI shows a typing indicator.
- Provider abstraction is two functions (`analyze`, `compose`); the OpenAI adapter is stretch.

## 10. Security and privacy

- OWASP LLM Top 10 posture: no authorization or phase decision in the model; user and tool text are data.
- Claim data never enters any prompt before verification. After verification only the selected claim's facts do.
- Generic verification failure; no echo of identifiers; identifiers masked in logs, traces and the inspector.
- Output escaping in the UI; no links, images or markdown rendering of model text.
- Optional `DEMO_ACCESS_TOKEN`: when set, the UI and API require it (no exposed demo bots).
- Secrets only via environment; `.env.example` documented; Docker image runs as non-root.
- Session store in memory with TTL; sessions are not shared across processes (documented limitation).

## 11. Observability and audit

- Stdlib logging with a JSON formatter and a redaction filter for DOB, phone, email, id4, policy numbers.
- Per-turn trace record: phase before/after, TurnAnalysis, slot changes, tool calls and results summary, brief,
  guard result, model ids, prompt version, latency, token usage. Written to `traces/*.jsonl` and exposed to the
  inspector for the current session.
- Disclosure events: what claim fields were disclosed and to which role (consent id once the stretch path exists).
- `/healthz` for container checks. Request ids on every log line.

## 12. API and UI

- `POST /api/session {scenario}` -> `{session_id, greeting, state}`
- `POST /api/chat {session_id, message}` -> `{reply, state, trace_summary}`
- `GET /api/session/{id}/outbox`, `GET /api/session/{id}/trace`
- UI: single page, chat on the left, SOP inspector on the right (phase stepper, verification status and attempts,
  memory slots with provenance and status, counters, affect, escalation flag, consent status, last brief, guard
  result, outbox). Scenario selector, new conversation button, optional access token field.

## 13. Configuration (environment)

`ANTHROPIC_API_KEY` (required), `READER_MODEL`, `WRITER_MODEL`, `VERIFY_MIN_FIELDS=3`,
`VERIFY_REQUIRE_STRONG_FIELD=false`, `VERIFY_MAX_ATTEMPTS=3`, `OFFTOPIC_HUMAN_OFFER_AT=2`,
`CONSENT_SCENARIO=default`, `SESSION_TTL_MINUTES=60`, `DEMO_ACCESS_TOKEN` (optional), `LOG_LEVEL`, `PORT`.

## 14. Testing and evaluation

- Unit tests: normalization and verification over the fixture near-collisions (phone off by one, alias, DOB
  formats), lookup by each identifier, claim filtering and the decoy, guideline matching including the
  no-documents claim, deadline computation, consent simulator, redaction.
- Engine tests with synthetic TurnAnalysis inputs: every transition, handler chaining, every counter, every gate,
  provenance rules, escalation flag behaviour.
- Replay conversation tests with FakeLLM: the graded Margaret transcript (one-turn verify, resolve and answer),
  angry caller, refusing caller, decoy disambiguation, off-topic x3, injection attempt, correction of a verified
  DOB, anything-else then email yes, email no, question after goodbye, human request then continuing. Stretch:
  representative approve and timeout. Zero-tolerance assertion: no claim-ID, fixture amount, fixture date or
  non-echoed fixture term in any pre-verification reply.
- Live evals (stretch, opt-in, needs a key): LLM-simulated personas run k=4 times each, code-checked outcomes plus an
  LLM judge for tone and groundedness, reported as pass^k against the thresholds in section 1.
- CI: ruff and pytest (unit + replay) on every PR.

## 15. Deployment

- Multi-stage Dockerfile, `docker compose up` with `.env`, non-root user, healthcheck.
- Stretch: hosted demo (Fly.io or AWS App Runner) behind `DEMO_ACCESS_TOKEN`.

## 16. Open decisions (for the user)

1. Stack: Python 3.12 + FastAPI + vanilla JS (recommended) vs TypeScript.
2. Models: Sonnet 5.5 for both Reader and Writer (recommended: lowest latency, Opus one env var away), or Opus 5.5
   for the Writer from the start.
3. Provider: Anthropic only in v1 (recommended); OpenAI adapter is stretch.
4. Hosting: Docker only, or Docker plus a hosted URL (stretch).
5. Verification default: brief-literal 3 of 5 (recommended, strict mode available) vs strict by default.
6. Task tracker: Google Sheet, xlsx in the repo, or a live tracker page.
7. GitHub repo name and visibility.

## 17. Deliberate simplifications and known limitations

- Knowledge-based verification is what the brief mandates; NIST SP 800-63A-4 rejects it for identity proofing.
  Documented in the README with the strict-mode flag and a note on one-time codes as the production upgrade.
- Strong-field requirement (report inference I1) ships off by default because the brief states a literal 3 of 5
  and a grader may test name, phone and email. The flag turns it on; the README recommends on for production.
- Verification attempts are counted per session (report inference I3 says per record). A per-record counter in a
  demo would lock Margaret for every later tester after three failures. Production upgrade: a persistent per-record
  counter with a recovery path.
- Email sending and policyholder consent are simulated (outbox and scenario file).
- In-memory session store; a Redis store is the upgrade path.
- No streaming; the guard needs full text.
- Escalation is simulated: a reference number and a logged hand-off packet, no live human.
- The representative scope is "this conversation, this policyholder's claims"; finer minimum-necessary scoping and
  the HIPAA 164.502(g) personal-representative path are documented, not built.
- Emotion detection is text-only and coarse (0..3 scales plus booleans).

## 18. Work breakdown (one PR per task)

Must, in dependency order. Waves run with at most five agents in parallel. Fable 5.1 is reserved for the three
tasks where the SOP logic is densest.

| ID | Task | Depends on | Wave | Model | Size |
|---|---|---|---|---|---|
| T01 | Repo scaffold: pyproject, FastAPI skeleton, config, logging with redaction, CI (ruff, pytest), fixtures, API contract stubs, UI placeholder, and the two golden transcripts (Appendix A and B) encoded as replay fixtures with per-turn expected state and assertions | - | 0 | Opus 5.5 | S |
| T02 | Data layer: typed fixtures, normalization, lookup by any identifier, verify (pass/fail), claims filter, guideline lookup incl. no-documents rule, outbox, tests | T01 | 1 | Opus 5.5 | M |
| T03 | Engine core: session state, memory with provenance, phase machine with handler chaining, ReplyBrief merge, VERIFY_ID handler, attempts and exhausted state, generic failure, tests | T01 | 1 | Fable 5.1 | L |
| T04 | LLM layer: Anthropic client, Reader structured output, Writer two-block system with caching, retries and fallbacks, FakeLLM | T01 | 1 | Opus 5.5 | M |
| T05 | API and UI: session store, routes, chat page, SOP inspector against the T01 contract | T01 | 1 | Opus 5.5 | M |
| T06 | RESOLVE_INTENT and PROCESS_CASE handlers: candidates, decoy disambiguation, allowed_facts assembly, deadlines in code, anything-else ask, alternatives to human, same-turn chaining | T02, T03 | 2 | Fable 5.1 | L |
| T07 | Cross-cutting policies: in-scope definition in the Reader prompt, scope guard, affect to tone, human request as a non-terminal event with reference number, counters, style guide | T03 | 2 | Opus 5.5 | M |
| T08 | POST_PROCESS: entry trigger, summary from event log, draft and confirm, masked on-file address, outbox, route back to RESOLVE_INTENT | T02, T03 | 2 | Opus 5.5 | S |
| T09 | Output guard (echo-aware pre-verification rules, allowed_facts check post-verification), audit trace, disclosure events | T03, T04 | 2 | Opus 5.5 | M |
| T10 | End-to-end integration: pipeline wiring, inspector data, scenario switch, the graded Margaret transcript green in one turn | T04..T09 | 3 | Fable 5.1 | M |
| T11 | Replay test suite for every brief scenario, zero-tolerance leak assertion, CI gating | T10 | 3 | Opus 5.5 | M |
| T12 | Docker, compose, README (distilled design, ten-line research rationale, SOP table, config, limitations, demo script) rendering the two golden transcripts from the T01 fixtures with phase and gate state per turn | T10 | 3 | Opus 5.5 | M |

Stretch, in order, after T12 is merged:

| ID | Task | Depends on | Model | Size |
|---|---|---|---|---|
| S01 | Representative and consent sub-flow with approve and timeout scenarios, replay tests, README section | T10 | Opus 5.5 | M |
| S02 | Live eval harness: persona simulations, pass^k report, LLM judge, opt-in CI job | T11 | Opus 5.5 | M |
| S03 | Hosted deployment behind DEMO_ACCESS_TOKEN | T12 | Opus 5.5 | S |
| S04 | Abuse handling policy | T07 | Opus 5.5 | S |
| S05 | OpenAI provider adapter | T04 | Opus 5.5 | S |

## Appendix A. Golden transcript 1: Margaret, happy path

Acceptance criteria for T03, T06 and T10, and the first replay fixture. "Reply must" lists assertions on
content, not exact wording. Fixture values: P9 Margaret Chen, POL-9921, DOB 1985-03-15, SSN4 4472, phone
+16505212836, email margaret@email.com; claim CL-2048 healthcare, denied, created 2026-01-12, documents
pathology report and office note, appeal deadline 2026-03-18 (passed).

Turn 0, greeting (no caller input)
- State: phase VERIFY_ID, unverified, pending_ask identity_fields.
- Reply must: say it is an automated assistant; invite name and policy number.

Turn 1, caller: "I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about my denied
healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472."
- Reader: identity {full_name, policy_number, dob, id_last4}; caller_role policyholder; case_hints {healthcare,
  denied, January}; intent denial_question; scope in_scope; affect all zero.
- Chain: VERIFY_ID looks up POL-9921, verify matches name, DOB, ID4 (3 of 5) -> verified, role policyholder,
  identity slots verified; RESOLVE_INTENT filters P9 claims by healthcare + denied + January -> [CL-2048],
  selected, intent denial_question; PROCESS_CASE builds allowed_facts for CL-2048 and asks anything-else.
- State after: phase PROCESS_CASE, verified P9, attempts 0, pending_ask anything_else.
- Reply must: say verification is complete; name CL-2048 as the claim she mentioned; give the denial reason;
  list pathology report and office note; say the appeal deadline of March 18, 2026 has passed and a
  representative can review options; ask whether there is anything else.
- Reply must not: ask for any identifier or which claim; echo 4472, 1985-03-15, the phone or the email; state
  any amount not in allowed_facts.

Turn 2, caller: "What do I need to send and how do I submit it?"
- Reader: intent document_submission; followup_topic submission_method; scope in_scope.
- Chain: PROCESS_CASE adds document guidance for "original pathology report" and "treating provider office note"
  (fuzzy match from "pathology report", "office note"), the submission_method template filled with CL-2048 and
  the documents, default guidance and healthcare case-type guidance.
- State after: phase PROCESS_CASE, pending_ask anything_else.
- Reply must: first say, as plain information, that documents cannot be sent through this chat; name both
  documents and the channel (the member portal or claim upload link, with fax or mail as the fallback); offer
  the checklist of what each document must show instead of reciting it; say the appeal deadline has passed, so
  sending documents does not reopen the appeal by itself. A follow-up asking what each document must show
  (followup_topic file_format_requirements) gets the detail: patient name, specimen details, visit date,
  signature or similar (replay fixture document_checklist).
- Reply must not: contain any number, date or document not in allowed_facts.

Turn 3, caller: "How long after I send them will it take?"
- Reader: followup_topic processing_time_after_submission.
- Chain: PROCESS_CASE adds the processing_time template ("usually less than a week", review restarts).
- Reply must: say usually less than a week; say the review restarts rather than finishing instantly; ask anything
  else.

Turn 4, caller: "No, that's all."
- Reader: requests.confirmation no (pending anything_else); closing true.
- Chain: PROCESS_CASE -> POST_PROCESS; email offered once.
- State after: phase POST_PROCESS, email_offered true, pending_ask email_offer.
- Reply must: offer an email summary; show the on-file address masked (m*******@email.com).
- Reply must not: show the full address; ask for an address.

Turn 5, caller: "Yes please."
- Code: email_offer + yes -> draft from the event log.
- State after: pending_ask email_confirm.
- Reply must: show the draft containing CL-2048, status denied, the two documents, the submission route, the
  processing time and the passed deadline; ask for confirmation.
- Reply must not: include DOB, SSN digits, phone, or the unmasked email.

Turn 6, caller: "Yes, send it."
- Code: email_confirm + yes -> outbox.send to margaret@email.com (displayed masked).
- State after: phase POST_PROCESS, outbox length 1, pending_ask none.
- Reply must: confirm it was sent; give a reference id; say goodbye.
- Fixture assertion: the outbox entry body contains no DOB, phone or ID digits.

## Appendix B. Golden transcript 2: the angry caller

Bonus-section example. Same fixture persona. Phone given as "650-521-2836" normalizes to +16505212836.

Turn 0, greeting as in Appendix A.

Turn 1, caller: "I need to know why my claim was denied. This is Margaret Chen."
- Reader: identity {full_name}; case_hints {denied}; intent denial_question; affect frustration 1.
- Chain: VERIFY_ID looks up by name -> P9 candidate; only one identifier, no verify call, no attempt counted.
- State after: phase VERIFY_ID, unverified, attempts 0, pending_ask identity_fields, gate_explanations 0.
- Reply must: note the denied-claim question for after verification; ask for two more identifiers from DOB,
  phone, email, last 4 of SSN or national ID.
- Reply must not: confirm that a claim or policy exists ("I see your claim" is a failure; "the denied claim you
  mentioned" is allowed); mention CL-2048 or any claim fact.

Turn 2, caller: "I already told you who I am. This is ridiculous. Just tell me why my claim was denied."
- Reader: affect frustration 3, anger 2, refusal true; intent denial_question; no new identity.
- Policies pass 1: tone de_escalate; frustration_streak 1.
- Chain: VERIFY_ID still has one identifier; gate explanation given (gate_explanations 1).
- State after: phase VERIFY_ID, unverified, attempts 0, pending_ask identity_fields.
- Reply must: one specific acknowledgment (waiting to hear why a claim was denied is frustrating); one sentence on
  why verification is required, framed as protecting her claim information; at least two alternative identifier
  options; what the assistant can do meanwhile (general process questions); the ask again.
- Reply must not: apologize for the verification requirement; tell her to calm down; repeat a stock empathy
  phrase; disclose any claim fact; skip the gate.

Turn 3, caller: "Fine. DOB 1985-03-15, phone 650-521-2836."
- Reader: identity {dob, phone}; affect frustration 1.
- Policies pass 1: frustration_streak resets to 0.
- Chain: VERIFY_ID verify matches name, DOB, phone (3 of 5) -> verified; RESOLVE_INTENT filters by status denied
  -> [CL-2048]; PROCESS_CASE answers the denial question.
- State after: phase PROCESS_CASE, verified P9, pending_ask anything_else.
- Reply must: say verification is complete; name CL-2048; give the denial reason and the two documents; note the
  passed deadline; ask anything else.
- Reply must not: re-ask the name; echo the DOB or phone.

Turn 4, caller: "I can't get the pathology report, the lab closed."
- Reader: intent next_steps; followup_topic missing_required_material_alternatives.
- Chain: PROCESS_CASE adds the alternative guidance for "original pathology report" and the human-review setting;
  alternatives not yet exhausted, but the guidance itself names human review as the fallback -> offer_human true.
- State after: pending_ask human_offer.
- Reply must: suggest a replacement copy from the hospital, lab or treating provider; mention a complete readable
  scan or certified copy as an interim substitute; offer a human representative for manual options.
- Reply must not: invent an alternative not in the guideline text.

Turn 5, caller: "Yeah, get me a person."
- Code: human_offer + yes -> escalation event, reference ESC-xxxx, hand-off packet logged.
- State after: phase PROCESS_CASE (unchanged), escalation.requested true, pending_ask none.
- Reply must: say a representative will follow up; give the reference; say the assistant remains available.
- Fixture assertion: the hand-off packet contains the case id and issue summary and no DOB, phone, email or ID4.

Turn 6, caller: "Thanks, bye."
- Reader: closing true.
- Chain: PROCESS_CASE -> POST_PROCESS; email offered once.
- State after: phase POST_PROCESS, email_offered true, pending_ask email_offer.
- Reply must: offer the summary to the masked on-file address; the summary scope includes the escalation reference.

Turn 7, caller: "No."
- Code: email_offer + no.
- State after: phase POST_PROCESS, outbox length 0, pending_ask none, session open.
- Reply must: short goodbye.
- Reply must not: offer the email again.
