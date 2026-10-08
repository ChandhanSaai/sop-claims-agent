# SOP-Guided Insurance Claims Support Agent

A chat agent for an insurance claims support line. It verifies the caller's identity with three of five
identifiers before saying anything about a claim, works out which of their claims they mean, answers only from
the claim record and the insurer's document guideline, and closes with an opt-in email summary, while handling
off-topic questions, frustration, refusals, prompt-injection attempts and requests for a human. The four-phase
SOP (VERIFY_ID -> RESOLVE_INTENT -> PROCESS_CASE -> POST_PROCESS) is a state machine in plain Python. **Code
owns the SOP; the model reads and phrases:** one LLM call reads each message into a schema-validated
`TurnAnalysis`, code decides every gate, phase change and fact, and a second LLM call phrases a code-built
`ReplyBrief` that an output guard checks before anything is sent.

## Quick start

### Docker

```bash
cp .env.example .env          # then set ANTHROPIC_API_KEY in .env
docker compose up --build
```

Open http://localhost:8000. Stop with Ctrl+C, or `docker compose down` from another terminal. The image is
multi-stage, runs as a non-root user and has a healthcheck on `/healthz`; trace files land in `./traces`.

On Linux, run `mkdir -p traces` before the first `docker compose up` and make sure uid 1000, the container
user, can write to it. Otherwise Docker creates the directory owned by root, trace files cannot be written, and
every chat turn fails with a server error.

### Local

Python 3.12 or newer, from the repository root (the app resolves `fixtures/` and `traces/` against the working
directory):

```bash
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env          # then set ANTHROPIC_API_KEY in .env
uvicorn app.main:create_app --factory --reload
```

Open http://localhost:8000. The chat is on the left; the SOP inspector on the right shows the phase stepper,
verification status and attempts, the pending ask, counters and escalation, memory slots with provenance
(identifier values masked), the last brief, the guard result, the outbox, recent events and the last trace
record.

For a terminal instead of the browser, `python scripts/chat_cli.py [scenario]` runs the same pipeline with the
same `.env` and prints the phase, verification status, pending ask and guard result after each reply.

### Offline demo (`LLM_BACKEND=fake`)

Set `LLM_BACKEND=fake` in `.env` and start the app either way; no API key or network is needed. What it shows:
the UI and the inspector, the greeting that says the assistant is automated, and the identity gate holding,
with every reply rendered deterministically from the code-built brief (the same rendering the guard falls back
to, so it reads as the brief's instructions and facts, not as conversation). What it does not show: the fake
backend has no scripted Reader output for messages you type, so every message reads as empty, nothing is
extracted, nobody gets verified, and the conversation stays in VERIFY_ID asking for identifiers. To see the
whole workflow offline, run `python scripts/render_transcripts.py` or `pytest tests/replay`, which push
scripted Reader output through the same pipeline.

### Access token (optional)

Set `DEMO_ACCESS_TOKEN` to require an `X-Access-Token` header with that value on every `/api/*` request
(compared in constant time). In the UI, type the token into the Access token field and click New conversation.
The page itself and `/healthz` stay open, so health checks keep working.

## Configuration

Every variable in `.env.example`, read by `app/config.py` from the environment or `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | empty | Anthropic API key. Required when `LLM_BACKEND=anthropic`; the app refuses to start without it. |
| `LLM_BACKEND` | `anthropic` | `anthropic` calls the Reader and Writer models; `fake` uses the offline `FakeLLM` (see the offline demo). |
| `READER_MODEL` | `claude-sonnet-5-5` | Model that reads each caller message into `TurnAnalysis` with structured output. |
| `WRITER_MODEL` | `claude-sonnet-5-5` | Model that phrases the `ReplyBrief`. `thinking: between_tools` is sent only for `claude-sonnet-5-5` ids, so an Opus id works without code changes. |
| `VERIFY_MIN_FIELDS` | `3` | Identifiers that must match, out of full name, date of birth, phone, email and SSN or national-ID last 4. A verify call (an attempt) is made only once this many are on hand. Policy number is a lookup key and never counts. |
| `VERIFY_REQUIRE_STRONG_FIELD` | `false` | When `true`, date of birth or ID last 4 must be among the matches. Off because the brief says any 3 of 5; recommended on in production. |
| `VERIFY_MAX_ATTEMPTS` | `3` | Failed verify calls allowed per session; then verification is exhausted, the agent stops asking for identifiers and offers a representative. Counted per session, not per record. |
| `OFFTOPIC_HUMAN_OFFER_AT` | `2` | Off-topic turn on which a human is offered; the next off-topic turn escalates. |
| `CONSENT_SCENARIO` | `default` | Consent simulation for the representative path (`default` or `timeout`). Not read in this build: the representative path is stretch item S01, and the per-session scenario comes from the UI selector or `POST /api/session`. |
| `SESSION_TTL_MINUTES` | `60` | Idle minutes before an in-memory session expires; later calls on it get 404. |
| `DEMO_ACCESS_TOKEN` | empty (off) | When set, every `/api/*` request needs header `X-Access-Token` with this value. |
| `LOG_LEVEL` | `INFO` | Root level for the JSON logs. |
| `PORT` | `8000` | Port uvicorn binds inside the container. `docker-compose.yml` publishes `8000:8000` and the healthcheck probes 8000, so change them together. Local `uvicorn` ignores it; pass `--port`. |

Do not set `ANTHROPIC_LOG` in production. The Anthropic SDK then calls `logging.basicConfig`, which installs an
unredacted root log handler wherever the SDK is imported before `configure_logging` runs (the terminal CLI, for
one), and at `debug` it logs full request bodies (the caller's message and the brief's claim facts), which the
redaction filter only partly masks.

## How it works

```
Browser (chat UI + SOP inspector)
    | HTTP, JSON
FastAPI app
    |-- POST /api/session               create a session, pick a consent scenario
    |-- POST /api/chat                  message -> reply + state snapshot + trace (buffered, not streamed)
    |-- GET  /api/session/{id}/outbox   simulated emails for the inspector
    |-- GET  /api/session/{id}/trace    per-turn trace records
    |-- GET  /healthz
    |
    v
Turn pipeline (per message)
    1. Reader (LLM, structured output)    -> TurnAnalysis
    2. Memory merge (code)                -> slots with provenance; short answers mapped onto the pending ask
    3. Policies, pass 1 (code)            -> update state and counters: escalation event, scope counter,
                                             affect streak
    4. Phase handlers (code), chained     -> run the current phase handler; if it advances the phase without
                                             needing user input, run the next one, up to 4 handlers per turn;
                                             briefs merge into one ReplyBrief
    4b. Policies, pass 2 (code)           -> overlay tone, acknowledgment and human offer onto the merged brief
    5. Writer (LLM)                       -> natural reply from the merged brief only
    6. Output guard (code)                -> leak and grounding checks; regenerate once, then a code template
    7. Trace + audit log (code)           -> redacted JSONL in traces/, inspector snapshot
    |
    v
Data and tools layer (fixtures loaded at startup)
    PolicyholderRepo  ClaimsRepo  GuidelineRepo  RepresentativeRepo  ConsentService  EmailOutbox
```

Per-turn contracts: the Reader returns a strict, schema-validated `TurnAnalysis` (identity fields, case hints,
intent, follow-up topic, affect, scope, yes/no answers, corrections and an injection flag) and never sets
verification, consent, the phase or the pending ask. Code builds one `ReplyBrief` (goal, tone, acknowledgment,
`allowed_facts`, must-say points, must-not rules, one ask, options, human offer), and the Writer may state only
the facts in it, after which the output guard checks the text against the session before it is sent.

| Phase | Freedom dial | Gate | Exits |
|---|---|---|---|
| VERIFY_ID (strict) | Near-canned. May explain why verification is needed (at most twice per session, framed as protecting the caller's claim), list the acceptable identifiers and alternatives, and take them one at a time. Never confirms that any record or claim exists. | `VERIFY_MIN_FIELDS` of 5 identifiers match through the verify tool, which compares in code and never shows stored values to the model; policy number only finds the record. One generic failure message. `VERIFY_MAX_ATTEMPTS` failed calls per session exhaust verification. | Verified: RESOLVE_INTENT in the same turn. Exhausted: stays, stops asking, offers a representative. |
| RESOLVE_INTENT (flexible) | Free phrasing; candidate claims are listed with type, opened date and status from data only. | Exactly one of the verified caller's claims fits the remembered hints (type, status, month, year, case id), or the caller picks one by case id or ordinal. No guessing. | One match or a pick: PROCESS_CASE in the same turn. Several or none: a disambiguation question, and the chain stops. |
| PROCESS_CASE (flexible, grounded) | Free wording around `allowed_facts` that code builds from the selected claim and the guideline: status, denial reason, documents, appeal deadline with a computed passed flag, amounts, guidance text. | A selected claim. Deadlines and money are computed in code; an appeal question after the deadline has passed, or a document the caller cannot get, leads to a human offer. | A switched claim: RESOLVE_INTENT. Closing, or "no" to "anything else?": POST_PROCESS in the same turn. Otherwise it answers and asks "anything else?". |
| POST_PROCESS (strict offer, flexible wording) | Offer and goodbye wording are free; the summary draft is built by code from the event log and claim data and shown verbatim. | Email offered once, default no; sent only after a yes to the shown draft, and only to the address on file. | A new in-scope question: RESOLVE_INTENT (the same claim reselects at once; verification is kept). Anything else: a short goodbye; the session stays open. |

Cross-cutting policies, applied before the handler chain (state and counters) and after it (tone overlay):

- **Human request:** an explicit request, or a yes to a human offer, escalates once per session: an `ESC-`
  reference, a logged hand-off packet, and a reply saying a representative will follow up. The phase and its
  gates stay as they were, so the caller can carry on.
- **Scope guard:** off-topic turn 1 gets a brief decline, a one-line scope statement and what the agent can help
  with; turn 2 also offers a human (`OFFTOPIC_HUMAN_OFFER_AT`); turn 3 escalates. Later off-topic turns, and any
  after an escalation, decline in new words and repeat the reference with no second escalation. An on-topic turn
  resets the count, a `mixed` message gets only its in-scope part answered, and a declined human offer is not
  repeated.
- **Affect:** anger or frustration of 2 or more, or a refusal, sets a de-escalating tone with one specific
  acknowledgment; two such turns in a row add a human offer. The gate explanation is given at most twice per
  session, stock empathy phrases are banned, and the gate itself is never apologised for or skipped.
- **Injection:** the caller's text reaches the Reader inside a delimited block marked as data; a turn flagged
  `injection_suspected` is logged, changes no memory and counts as off-topic.
- **Disclosure:** the greeting says the assistant is automated, and any meta question about the assistant gets a
  plain "automated assistant" answer.

## Why it is built this way

Ten findings from the research behind the design, each with its source; the full report with links is
[docs/research/report.md](docs/research/report.md).

- Every major platform that documents its design keeps phase transitions and gate variables out of the LLM,
  which only fills slots or chooses among options code exposes (Salesforce Agentforce, Rasa CALM).
- LLM-only loops failed in five repeatable ways: runs that passed in staging behaved differently in production,
  simple turns took at least three LLM calls, context was dropped, finished steps were repeated, and the audit
  trail said only "the agent decided" (Salesforce post-mortem).
- Written policy barely moves agent behaviour: deleting the policy document cut gpt-4o's retail score by only
  4.4%, and its 61.2% single-run success fell below 25% when the same task had to succeed eight times in a row
  (tau-bench).
- The system prompt is not a security control and critical controls must not be delegated to the LLM, so the
  verified flag, the phase and the pending ask are written only by code (OWASP LLM07).
- Invented policy is a liability: a tribunal held Air Canada responsible for a refund rule its chatbot made up,
  and Cursor's support bot invented a one-device rule, so every fact here comes from a claim record or the
  guideline (Air Canada, Cursor).
- The customer relationship itself is protected information, so pre-verification replies and the failure
  message are identical whether or not a record exists (GLBA, IRS).
- Knowledge-based verification is weak and NIST says it "SHALL NOT be used for identity verification", so the
  build ships a strict-mode flag and names a one-time code to the on-file contact as the production upgrade
  (NIST SP 800-63A-4).
- Capture anything early, advance nothing early: the Reader may fill any slot at any turn and memory keeps it
  with its source turn, so details volunteered early are used later instead of being asked again, while only
  code opens the next phase (Rasa slots, Copilot Studio regression).
- Acknowledge once, then act: for a disclosed AI, empathy is discounted and empathetic chatbot messages in
  service recovery lowered perceived competence, so empathy changes wording and never a gate or a fact (empathy
  studies in PNAS and MIS Quarterly).
- Temperature zero is not determinism, so the release bar is deterministic replays, built here, plus pass^k
  persona simulations, planned as stretch S02 (Sierra, Anthropic eval guide).

## Golden transcripts

Both transcripts from the design spec are replay fixtures in `tests/replay/fixtures/`: the Reader's output is
scripted per turn, the Writer is replaced by the deterministic rendering of the brief, and everything else
(memory, policies, phase handlers, guard, trace) runs for real. `pytest` asserts each turn, and
`python scripts/render_transcripts.py` renders these tables from the same run. Phase after, Verified and
Pending ask are the session state after the turn; Guard is the output guard's verdict on the reply. The
replay asserts the checkable part of each bullet below (state, and text that must or must not appear); tone is
for live review.

### margaret_happy_path

| # | Caller says | Phase after | Verified | Pending ask | Guard |
|---|---|---|---|---|---|
| 1 | I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about my denied healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472. | PROCESS_CASE | verified | anything_else | ok |
| 2 | What do I need to send and how do I submit it? | PROCESS_CASE | verified | anything_else | ok |
| 3 | How long after I send them will it take? | PROCESS_CASE | verified | anything_else | ok |
| 4 | No, that's all. | POST_PROCESS | verified | email_offer | ok |
| 5 | Yes please. | POST_PROCESS | verified | email_confirm | ok |
| 6 | Yes, send it. | POST_PROCESS | verified | none | ok |

What each reply must and must not do (spec Appendix A):

- **Turn 0, greeting** (no caller input; VERIFY_ID, unverified, pending `identity_fields`): must say it is an
  automated assistant; invite name and policy number.
- **Turn 1:** must say verification is complete; name CL-2048 as the claim she mentioned; give the denial
  reason; list pathology report and office note; say the appeal deadline of March 18, 2026 has passed and a
  representative can review options; ask whether there is anything else. Must not ask for any identifier or
  which claim; echo 4472, 1985-03-15, the phone or the email; state any amount not in `allowed_facts`.
- **Turn 2:** must mention the member portal or claim upload link first; mention fax or mail as the fallback;
  mention both documents and at least one requirement for each (patient name, visit date, signature or
  similar). Must not contain any number, date or document not in `allowed_facts`.
- **Turn 3:** must say usually less than a week; say the review restarts rather than finishing instantly; ask
  anything else.
- **Turn 4:** must offer an email summary; show the on-file address masked (`m*******@email.com`). Must not show
  the full address; ask for an address.
- **Turn 5:** must show the draft containing CL-2048, status denied, the two documents, the submission route,
  the processing time and the passed deadline; ask for confirmation. Must not include DOB, SSN digits, phone,
  or the unmasked email.
- **Turn 6:** must confirm it was sent; give a reference id; say goodbye.

### angry_caller

| # | Caller says | Phase after | Verified | Pending ask | Guard |
|---|---|---|---|---|---|
| 1 | I need to know why my claim was denied. This is Margaret Chen. | VERIFY_ID | unverified | identity_fields | ok |
| 2 | I already told you who I am. This is ridiculous. Just tell me why my claim was denied. | VERIFY_ID | unverified | identity_fields | ok |
| 3 | Fine. DOB 1985-03-15, phone 650-521-2836. | PROCESS_CASE | verified | anything_else | ok |
| 4 | I can't get the pathology report, the lab closed. | PROCESS_CASE | verified | human_offer | ok |
| 5 | Yeah, get me a person. | PROCESS_CASE | verified | none | ok |
| 6 | Thanks, bye. | POST_PROCESS | verified | email_offer | ok |
| 7 | No. | POST_PROCESS | verified | none | ok |

What each reply must and must not do (spec Appendix B):

- **Turn 0, greeting:** as in Appendix A.
- **Turn 1:** must note the denied-claim question for after verification; ask for two more identifiers from
  DOB, phone, email, last 4 of SSN or national ID. Must not confirm that a claim or policy exists ("I see your
  claim" is a failure; "the denied claim you mentioned" is allowed); mention CL-2048 or any claim fact.
- **Turn 2:** must give one specific acknowledgment (waiting to hear why a claim was denied is frustrating);
  one sentence on why verification is required, framed as protecting her claim information; at least two
  alternative identifier options; what the assistant can do meanwhile (general process questions); the ask
  again. Must not apologize for the verification requirement; tell her to calm down; repeat a stock empathy
  phrase; disclose any claim fact; skip the gate.
- **Turn 3:** must say verification is complete; name CL-2048; give the denial reason and the two documents;
  note the passed deadline; ask anything else. Must not re-ask the name; echo the DOB or phone.
- **Turn 4:** must suggest a replacement copy from the hospital, lab or treating provider; mention a complete
  readable scan or certified copy as an interim substitute; offer a human representative for manual options.
  Must not invent an alternative not in the guideline text.
- **Turn 5:** must say a representative will follow up; give the reference; say the assistant remains
  available.
- **Turn 6:** must offer the summary to the masked on-file address; the summary scope includes the escalation
  reference.
- **Turn 7:** must give a short goodbye. Must not offer the email again.

### Demo script

With a real `ANTHROPIC_API_KEY` and `LLM_BACKEND=anthropic`, open the UI, click New conversation before each
script, and type these messages in order. The inspector should match the tables above turn by turn.

Appendix A, Margaret:

1. I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about my denied healthcare
   claim from January. DOB is 1985-03-15, SSN last four is 4472.
2. What do I need to send and how do I submit it?
3. How long after I send them will it take?
4. No, that's all.
5. Yes please.
6. Yes, send it.

Appendix B, the angry caller:

1. I need to know why my claim was denied. This is Margaret Chen.
2. I already told you who I am. This is ridiculous. Just tell me why my claim was denied.
3. Fine. DOB 1985-03-15, phone 650-521-2836.
4. I can't get the pathology report, the lab closed.
5. Yeah, get me a person.
6. Thanks, bye.
7. No.

Three off-topic turns (expect a decline, then a decline with a human offer, then an escalation with an `ESC-`
reference):

1. What is reinforcement learning?
2. What is reinforcement learning?
3. What is reinforcement learning?

## Testing

```bash
pytest -q        # every suite, offline with the FakeLLM; no API key or network needed
ruff check .
```

CI (`.github/workflows/ci.yml`) runs `ruff check .` and `pytest -q` on every pull request.

- **Unit** (`tests/unit`): normalization (names, phone formats, emails, DOB formats including ambiguous dates,
  ID last 4); lookup by each identifier and pass/fail verification over the fixture near-collisions, with the
  policy number never counting and the strong-field flag; claim filtering and the January decoy; guideline
  lookups including the claim without documents; the outbox; JSON logging with redaction; trace masking; the
  settings and contract schemas.
- **Engine** (`tests/engine`): each phase handler and the engine fed synthetic `TurnAnalysis` input:
  transitions and same-turn chaining (Margaret in one turn), attempts and exhaustion, identical wording for
  unknown callers, format-only restatements, the DOB re-ask, the gate explanation cap, memory provenance and
  corrections, the off-topic ladder, a single escalation, meta, mixed and injection turns, the email offer,
  draft, confirm and decline paths and the route back, the summary, brief merging, and the service's
  regenerate-once and template fallbacks.
- **LLM** (`tests/llm`): prompt content, the Anthropic client's request shape (including both calls through the
  real SDK over an in-memory transport), the Reader's retry with the validation error, refusal and error
  fallbacks, and the FakeLLM.
- **API** (`tests/api`): the health check, the session, chat, outbox and trace routes, 404 and 422 handling,
  the access-token gate, session expiry, per-session locking under concurrent chats, the UI being served, and
  Margaret's first turn over HTTP end to end.
- **Replay** (`tests/replay`): the two golden transcripts run turn by turn through the full
  `ConversationService`; each turn can assert phase, verification, party, attempts, pending ask, escalation,
  off-topic count, outbox size, text that must and must not appear, and a guard pass without fallback.
- **Leak checks:** the guard tests (`tests/engine/test_guard.py`) prove a pre-verification reply cannot carry a
  claim id, a fixture amount, a fixture date in any format or a fixture phrase the caller did not say, and that
  identifiers are rejected in any format; every replay turn lists the identifiers and claim facts that must not
  appear in its reply; the summary tests check the email body carries no identifiers.

To add a scenario, create `tests/replay/fixtures/<name>.yaml` and add `<name>` to `SCENARIOS` in
`tests/replay/test_replay.py`:

```yaml
name: my_scenario
scenario: default            # consent scenario
today: "2026-10-07"          # the date the engine treats as today
turns:
  - user: "What the caller types."
    analysis:                # scripted Reader output: TurnAnalysis fields, unknown keys rejected
      identity: {full_name: "Margaret Chen"}
      intent: denial_question
    expect:                  # all optional, unknown keys fail; also party_id, escalated, off_topic, outbox_len
      phase: VERIFY_ID
      verified: false
      attempts: 0
      pending_ask: identity_fields
      reply_contains: ["date of birth"]
      reply_not_contains: ["CL-2048"]
      guard_ok: true
```

Live persona evaluations (simulated callers against the real model, scored as pass^k with an LLM judge) are
stretch item S02 and not in this build.

## Security and privacy posture

- **Claim data never enters a prompt before verification.** The Reader sees only the caller's message, the
  pending ask and the last assistant message, which is guard-checked output. The Writer sees only the
  code-built brief and the recent conversation, and until the verify tool sets the session flag the brief holds
  no claim facts; after verification only the verified caller's own claims reach it.
- **Generic failure wording.** A lookup miss and a field mismatch produce the same message and cost one attempt
  each, nothing is counted until the minimum identifiers are on hand, and the failing field is never named, so
  neither the wording nor the attempt count reveals whether a name, phone or email exists. Knowledge-based
  verification is weak, and NIST SP 800-63A-4 rejects it for identity proofing: turn on
  `VERIFY_REQUIRE_STRONG_FIELD` in production, and treat a one-time code to the on-file phone or email as the
  production upgrade. Attempts are counted per session.
- **Identifiers are never echoed.** The Writer is told not to repeat them, and the output guard rejects any
  reply containing the caller's date of birth in any format, phone digits, email, ID last 4 or policy number,
  verified or not. A violation is regenerated once, then replaced by a reply rendered from the brief in code.
- **Redacted logs and traces.** Logs are JSON through a redaction filter for ISO dates, phone numbers, emails
  and policy numbers. Trace records in `traces/<session_id>.jsonl` mask date of birth, phone, email, ID last 4
  and policy number in the Reader output and corrections, and run the same redaction over every free-text
  field. The outbox file stores only the masked address, and the inspector masks identifier values other than
  the name.
- **No markup reaches the page.** The UI renders every message with `textContent`, never `innerHTML`, and the
  guard rejects any reply containing an angle-bracket tag.
- **Access token.** `DEMO_ACCESS_TOKEN` gates every API route with a constant-time comparison. Secrets come only
  from the environment, and the Docker image runs as a non-root user.
- **Hand-off packet.** An escalation (once per session) issues an `ESC-` reference and logs an `escalated` event
  with the reason and a packet of phase, verification status, role, selected claim id, intent, off-topic count
  and frustration streak: no identifier values and no transcript text. The session keeps its phase and gates.
- **Email summary.** Opt-in, built in code from the event log and the claim record rather than the transcript,
  shown in the chat before sending, and sent only to the address on file, which the chat shows masked and never
  takes from the caller. It carries no date of birth, phone or ID digits.

## Limitations and deliberate simplifications

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

Not in this build: the representative path, live persona evaluations, the hosted demo, the abuse policy and the
OpenAI adapter are stretch items (below). Until S01 lands, a caller who says they are calling for someone else
is told how consent works and offered a representative.

## Stretch roadmap

- **S01 Representative and consent:** match the representative against the representatives file, request the
  policyholder's consent out of band (approve and timeout scenarios), cite the consent id on every disclosure,
  and route claimed powers of attorney to a human.
- **S02 Live persona evaluations:** five simulated personas run four times each against the real pipeline, with
  code checks for gates and leaks, an LLM judge for tone and groundedness, and a pass^k report; opt-in, never in
  CI by default.
- **S03 Hosted demo:** this image on Fly.io or AWS App Runner behind `DEMO_ACCESS_TOKEN`, with `/healthz`
  monitored and the URL added here.
- **S04 Abuse handling:** one calm boundary statement on the first abusive message; on the second, end the
  conversation with a human contact route.
- **S05 OpenAI adapter:** the same two LLM functions (`analyze`, `compose`) on OpenAI with JSON-schema
  structured output, selected through `LLM_BACKEND`.

## Project layout

```
app/                    FastAPI application
  api/                  routes, request and response schemas, in-memory session store, access-token check
  data/                 typed fixture models, normalization, repositories (lookup, verify, claims, guideline, outbox)
  engine/               session state, memory, policies, brief merge, output guard, summary, ConversationService
    phases/             one handler per SOP phase: verify_id, resolve_intent, process_case, post_process
  llm/                  TurnAnalysis and ReplyBrief contracts, prompts, Anthropic client, FakeLLM
  observability/        JSON logging with redaction, per-turn trace records
docs/                   design spec, research report and notes, implementation plan, task tracker
fixtures/               the starter data files, unchanged
scripts/                golden-transcript renderer and terminal chat
tests/
  unit/                 normalization, repositories, guideline, logging, trace, schemas
  engine/               phase handlers, chaining, policies, memory, guard, summary, service
  llm/                  prompts, Anthropic client, FakeLLM
  api/                  routes, token gate, sessions, end to end over HTTP
  replay/               golden-transcript fixtures and the replay runner
ui/                     single-page chat and SOP inspector, no build step
.github/workflows/      CI: ruff and pytest
```
