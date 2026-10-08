# Architecture patterns for SOP-bound LLM support agents (flow engine + LLM)

*Compiled 2026-10-07. Source-type tags:*
- *[docs]: official product or framework documentation.*
- *[eng]: vendor engineering or architecture write-up.*
- *[mkt]: vendor marketing or product page.*
- *[bench]: benchmark run by a vendor.*
- *[paper]: arXiv or peer-reviewed paper.*
- *[forum]: community Q&A.*
- *[press]: press release or secondary news.*

*"Accessed" means the page shows no date; content is as fetched on 2026-10-07. "Search extract" means the text came from a search-engine extract of the cited page, not from a full fetch, so treat it as less certain.*

## 1. How do the major vendors and frameworks split work between deterministic code and the LLM, and what exactly is the LLM allowed to decide?

### Takeaway
The mature platforms have all landed on the same division of labour. Deterministic code owns state, step order, gating of side-effecting actions, and business rules. The LLM is confined to three jobs:
- turning the user turn into a constrained structure (commands, slot values, a topic or intent, or guideline matches);
- choosing among options that code has already filtered;
- wording the reply.

The platforms differ mainly in which direction they came from:
- **Deterministic builders that added LLMs** as parsers and rephrasers: Rasa, Dialogflow, Lex, Cognigy, Kore.ai, Voiceflow.
- **LLM-first platforms that added deterministic gates**: Agentforce, Copilot Studio, Fin, Decagon, Ada, Sierra.
- **In between**: Parlant keeps the LLM in charge but filters its context on every turn.

### Cited Findings

#### Rasa CALM (the LLM emits commands into a deterministic flow engine)
- **Design statement.** "We describe a system for building task-oriented dialogue systems combining the in-context learning abilities of large language models (LLMs) with the deterministic execution of business logic." The LLM translates the conversation into a small command language (a DSL), and those commands drive deterministic business logic. [paper] — [Bocklisch, Werkmeister, Varshneya, Nichol, "Task-Oriented Dialogue with In-Context Learning", arXiv 2402.12234, 2024-02-19](https://arxiv.org/abs/2402.12234)
- **The LLM's whole job is dialogue understanding.** The command generator "ingest[s] information about a conversation so far. It outputs a sequence of `commands` that represent how the user wants to progress the conversation". Examples:
  - `StartFlow("transfer_money")`
  - `SetSlot(slot_name, True)`
  - `[SetSlot(slot_name, True), StartFlow("check_balance")]` for "yes. Oh what's my balance?"

  [docs] — [Rasa docs, LLM Command Generators (accessed)](https://rasa.com/docs/reference/config/components/llm-command-generators/)
- **The shipped prompt allows exactly six actions:** `start flow`, `set slot` ("Can be used to correct and change previously set values"), `disambiguate flows`, `search and reply` (knowledge base or off-topic), `cancel flow`, and `repeat message`. [docs] — [Rasa docs, LLM Command Generators, SearchReadyLLMCommandGenerator prompt template](https://rasa.com/docs/reference/config/components/llm-command-generators/)
- **What the prompt receives and how it is told to behave.** It gets JSON describing the available flows and slots, plus a "Current State" block (`active_flow`, `requested_slot`, current slot values). Its rules include:
  - "Only start a flow if the user's message is clear and fully addressed by that flow's description"
  - "Do not cancel any flow unless the user explicitly requests it"
  - "Only use information provided by the user"
  - "Strictly adhere to the provided action format"
  - "Focus on the last message and take it one step at a time"

  [docs] — [Rasa docs, LLM Command Generators, SearchReadyLLMCommandGenerator prompt template](https://rasa.com/docs/reference/config/components/llm-command-generators/)
- **Current generator versions.** The recommended generators are `CompactLLMCommandGenerator` and `SearchReadyLLMCommandGenerator`. The older `SingleStepLLMCommandGenerator` and `MultiStepLLMCommandGenerator` "are deprecated and will be removed in Rasa 4.0.0". SearchReady "is designed to prioritize flows over knowledge questions and small talk." [docs] — [Rasa docs, LLM Command Generators](https://rasa.com/docs/reference/config/components/llm-command-generators/)
- **Flow retrieval keeps the prompt small.** "By default, CALM does not include all possible flows in the LLM prompt." Only flows relevant to the current state are included, so that "the input context size does not linearly scale up with the size of the assistant." [docs] — [Rasa docs, Command Generator](https://rasa.com/docs/pro/customize/command-generator/); [Rasa docs, LLM Command Generators](https://rasa.com/docs/reference/config/components/llm-command-generators/)
- **Classic NLU and the LLM work together.** Since version 3.12, the `NLUCommandAdapter` and the LLM generator can both issue commands in the same turn. If they conflict over the same slot, or try to start different flows, "the command issued by the `NLUCommandAdapter` is prioritized." The `minimize_num_calls` setting (on by default) skips the LLM call entirely when NLU has already issued a `StartFlow` or a `SetSlot` for the active `collect` step. [docs] — [Rasa docs, LLM Command Generators](https://rasa.com/docs/reference/config/components/llm-command-generators/)
- **What stays deterministic.** Flows hold the business logic. Conversation repair runs through built-in "patterns": CALM ships "a default behavior for every conversation repair case that works out-of-the-box. Each case is handled through a pattern which is a special flow." The patterns are `pattern_correction`, `pattern_cancel_flow`, `pattern_continue_interrupted`, `pattern_skip_question`, `pattern_chitchat`, `pattern_clarification`, `pattern_completed`, `pattern_cannot_handle`, `pattern_human_handoff`, `pattern_internal_error`, and `pattern_search`. [docs] — [Rasa docs, Patterns (accessed)](https://rasa.com/docs/reference/primitives/patterns/)
- **Replies are templates by default.** An optional Contextual Response Rephraser lets an LLM rewrite a template, either per response (`metadata: rephrase: True`) or for all responses (`rephrase_all: true`). [docs] — [Rasa docs, Contextual Response Rephraser (accessed)](https://rasa.com/docs/reference/primitives/contextual-response-rephraser/)

#### Parlant (open-source "conversation control layer": the LLM stays in charge, but its context is filtered every turn)
- **The problem it targets.** System prompts: "the more instructions you add to a prompt, the faster your agent stops paying attention to any of them." Routed graphs: "the more routing you add, the more fragile it becomes when faced with the chaos of natural interactions." [docs/mkt] — [Parlant GitHub README (accessed)](https://github.com/emcie-co/parlant)
- **How it works.**
  - Rules are written as guidelines, i.e. condition/action pairs.
  - Each turn the engine works out which guidelines apply and puts only those in the prompt ("the engine filters context relevance, not the LLM").
  - Tools fire only when their associated guideline matches.
  - It also provides canned responses, a glossary, and OpenTelemetry tracing that logs every guideline match.

  [docs] — [Parlant GitHub README](https://github.com/emcie-co/parlant)
- **Journeys are its SOP primitive.** A journey is built from chat states, tool states, and fork states, joined by direct or conditional transitions. "The agent strives to follow the flow you've defined, but may jump multiple states (if the conditions for doing so apply), revisit previous ones, or adjust its pace based on how the customer interacts." Guidelines override journey states because they are "treated as more specific behavioral overrides". [docs] — [Parlant docs, Journeys (accessed)](https://www.parlant.io/docs/concepts/customization/journeys)
- **What the LLM decides.** Whether guideline and journey conditions currently hold, journey-state progression, tool arguments, and the wording of the reply. In STRICT composition mode it can only select a canned response. [docs] — [Parlant docs, Message Generation](https://www.parlant.io/docs/engine-internals/message-generation/)
- **Relationship primitives between guidelines.** For example, `refund_guideline.prioritize_over(upsell_guideline, promo_guideline)` and `compliance_guideline.depend_on(identity_check, account_lookup)`. [docs] — [Parlant 3.2 release blog (n.d.; between the 2025-08-15 3.0 release and the 2026-04-28 3.3.2 release)](https://parlant.io/blog/parlant-3-2-streaming-responses/); [Parlant releases](https://github.com/emcie-co/parlant/releases)

#### Sierra (Agent OS: modular tasks, a "constellation" of models, and supervisor agents)
- **Many models, picked per task.** "Agents built on Sierra are assembled using 15+ frontier, open-weight, and proprietary models, depending on the job to be done." Tasks are grouped by what they demand:
  - low-latency tool calling;
  - high-precision classification (for example, spotting suspicious behaviour);
  - long-context policy reasoning;
  - "pitch-perfect tone".

  [eng] — [Sierra blog, "Constellation of models", 2025-12-03](https://sierra.ai/blog/constellation-of-models)
- **Modular tasks, with supervisors around the riskier ones.** "Agent OS is built around modular task abstractions that isolate responsibilities... compose them from cleanly separated capabilities — retrieval, classification, tools, policies, and tone. Certain tasks get more 'agency', greater room to reason, reflect, and use tools. This level of agency is enabled by employing supervisors to enforce guardrails, policies, and quality checks." [eng] — [Sierra, Constellation of models, 2025-12-03](https://sierra.ai/blog/constellation-of-models)
- **Supervisor agents check inputs and outputs.** "Every production agent built on Sierra is managed by several supervisory agents, which ensure they stick to the right policies while also remaining flexible." One supervisor detects threats in user input. Others "audit an agent's every action and response, checking for behavior that's not compliant with policies or harmful language." [eng] — [Sierra blog, "From LLMs to enterprise-grade agents", 2025-10-02](https://sierra.ai/blog/enterprise-grade-agents)
- **Agent SDK.**
  - Composition: "Compose AI agents by mixing and matching skills—like triage, respond, and confirm—into complex workflows."
  - Per-workflow freedom: "Define the degree of flexibility your agent should exhibit for each workflow, allowing for varying levels of creativity and determinism."

  [mkt] — [Sierra Agent SDK product page (accessed)](https://sierra.ai/product/agent-sdk)

#### Google Dialogflow CX / Conversational Agents / ADK
- **Google's three tiers.** "Dialogflow CX agents always use language models for understanding end-user intention, but you can decide whether and how language models are used for agent responses."
  - *Deterministic flows*: they "use language models to understand end-user intention, which can be non-deterministic. However, once intention is established, you have complete control over the conversation flow and agent responses."
  - *Partly generative*: Generators (LLM-written responses, e.g. "conversation summarization") and Generative fallback (used when input doesn't match an expected intent).
  - *Fully generative*: Playbooks and data stores.

  [docs] — [Google Cloud docs, "Generative versus deterministic" (last updated 2026-10-05)](https://docs.cloud.google.com/dialogflow/cx/docs/generative-deterministic)
- **Playbooks.**
  - A playbook has a goal, instructions ("the process steps that the agent must take to accomplish the goal"), examples ("effectively few-shot prompt examples"), and input/output parameters.
  - *Task* playbooks are compositional: they call each other and pass parameters.
  - *Routine* playbooks are sequential stages that can hand off to flows. They have "improved latency" because transitions happen "within a single conversational turn".

  [docs] — [Google Cloud docs, Playbooks (accessed)](https://docs.cloud.google.com/dialogflow/cx/docs/concept/playbook)
- **Community advice on playbook vs flow.** "A flow could handle the logic you have shown, and would be more reliable as it is deterministic." [forum, search extract] — [Google Developer forums, "Cannot transition from a playbook to a flow" (2025)](https://discuss.google.dev/t/cannot-transition-from-a-playbook-to-a-flow/183811)
- **ADK workflow agents.** ADK is Google's Agent Development Kit. Its Sequential, Parallel and Loop agents "determine the execution sequence according to their type... without consulting an AI model for assistance with the orchestration. This approach results in deterministic and predictable execution patterns." In ADK 2.0 these templates are "superseded by more flexible workflow structures, including graph-based workflows and dynamic workflows." [docs] — [Google ADK docs, Workflow agents (accessed)](https://adk.dev/agents/workflow-agents/)

#### Microsoft Copilot Studio (classic topics vs generative orchestration)
- **Two modes.**
  - *Classic orchestration* triggers the topic whose trigger phrases best match. Authors collect inputs with Question nodes and reply with Message nodes.
  - *Generative orchestration* is the default for new agents. It picks topics, tools, agents and knowledge "based on the description" and can chain several of them for multi-intent requests. It "can automatically generate questions to prompt users for any missing information required to fill inputs," and it writes the final response.

  [docs] — [Microsoft Learn, "Orchestrate agent behavior with generative AI" (updated 2026-08-26)](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-generative-actions)
- **Documented limits that push strict slot collection back into deterministic nodes.**
  - "Tools and topics don't yet support custom entities (closed lists and regex entities) as input parameters. To collect information by using a custom entity, use a Question node in a topic."
  - Generative mode doesn't call the "Multiple Topics Matched" disambiguation topic.
  - When topic descriptions overlap, "the overlap makes that selection unpredictable."

  [docs] — [Microsoft Learn, generative orchestration](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-generative-actions)
- **Recommended pattern.** Have a topic return its result as an output variable instead of messaging the user, so the orchestrator writes the reply. Also "return an answered-state output too so the orchestrator doesn't answer the same request again." [docs] — [Microsoft Learn, generative orchestration](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-generative-actions)

#### Salesforce Agentforce (from the Atlas reasoning loop to Agent Script and Agent Graph "hybrid reasoning")
- **Two kinds of instruction in one script.** The new Agentforce works by "separating deterministic execution from LLM reasoning." Agent Script is a DSL that mixes two kinds of instruction:
  - "Deterministic logic instructions define conditions and action sequences that execute as code with no LLM involvement."
  - "Prompt instructions define natural language guidance that the LLM interprets at runtime."

  [docs/eng] — [Salesforce Architects, "Hybrid Reasoning with New Agentforce Builder and Agent Script" (2026)](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- **Execution is a state machine.** The script compiles to an Agent Graph. The Atlas Reasoning Engine "is a state machine executor. On each turn, it traverses the Agent Graph based on session state, executes deterministic nodes as code, and triggers LLM calls only where prompt instructions are present." Generally available since February 2026. [docs/eng] — [Salesforce Architects, "Hybrid Reasoning with New Agentforce Builder and Agent Script" (2026)](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- **Governing rule.** "If you can express a decision as code, it should be written as logic. If the decision requires judgment, interpretation, or natural language generation, the LLM should handle it." [docs] — [Salesforce Architects, Hybrid Reasoning](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- **The LLM is called at eight points; transitions are never among them.**
  - subagent classification (and "a short-circuit path can bypass the full LLM call when classification is unambiguous");
  - agent reasoning (choosing the next action);
  - response generation;
  - groundedness validation;
  - action simulation;
  - structured output generation;
  - localization;
  - progress indicators.

  "Graph traversal and state transitions never involve the LLM." [docs] — [Salesforce Architects, Hybrid Reasoning](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- **Guaranteed code blocks around the LLM.** The `before_reasoning` and `after_reasoning` blocks are fully deterministic. Authentication and entitlement checks belong in `before_reasoning` "because you want those verified before the LLM sees any tools." In Salesforce's words: "When you write a prompt instruction telling the LLM to 'always run' an action, that's a suggestion... When you place a `run` directive in `before_reasoning`, that's code." [docs] — [Salesforce Architects, Hybrid Reasoning](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- **Engineering framing.**
  - "Orchestration as design-time configuration, not runtime improvisation."
  - "Reliable multi-agent systems demand explicit choreography, not more prompt incantations."
  - Two coordination primitives: handoff (the full conversation context is passed to another agent) and delegation (a "concierge" orchestrator farms out subtasks).

  [eng] — [Phil Mui, Salesforce Engineering, "Agentforce's Agent Graph: Toward Guided Determinism with Hybrid Reasoning" (byline "Oct 20", year not printed; refers to Agent Script and "last week at Dreamforce")](https://engineering.salesforce.com/agentforces-agent-graph-toward-guided-determinism-with-hybrid-reasoning/)

#### Amazon Lex V2 and Bedrock Agents
- **Lex Assisted NLU.** LLMs are used "to improve intent classification and slot resolution while staying within your bot's configured intents and slots."
  - *Primary mode*: the LLM is the main interpreter of intent and slots.
  - *Fallback mode*: the LLM is used for intent only "if the confidence score determined by NLU is lower than the configured threshold or otherwise routing to the FallbackIntent," and for slots only "if the traditional NLU does not capture a value."

  [docs] — [AWS docs, Assisted NLU (accessed)](https://docs.aws.amazon.com/lexv2/latest/dg/assisted-nlu.html)
- **Assisted slot resolution (launched November 2023)** works only on built-in slot types: AMAZON.Alphanumeric (without regex), City, Country, Date, Number, PhoneNumber, and Confirmation. [docs, search extract] — [AWS docs, assisted slot resolution](https://docs.aws.amazon.com/lexv2/latest/dg/assisted-slot.md); [AWS What's New, Nov 2023](https://aws.amazon.com/about-aws/whats-new/2023/11/assisted-slot-resolution-generative-ai)
- **Bedrock Agents "return of control".** An action group configured with `customControl: RETURN_CONTROL` changes who executes the action:
  - the agent still elicits the parameters from the user;
  - it then returns them in `invocationInputs` with an `invocationId`, instead of executing anything;
  - the application runs the action and sends the results back in `sessionState.returnControlInvocationResults`.

  A separate "Get user confirmation before invoking action" option also exists. [docs] — [AWS Bedrock docs, Return control to the agent developer (accessed)](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-returncontrol.html)

#### Decagon (Agent Operating Procedures, or AOPs)
- **What an AOP is.** AOPs "are natural language instructions that compile into validated workflows for AI agents."
  - On the split of responsibilities: "The AI interprets messy, real-world customer input. The code-based logic ensures that critical actions are executed correctly every time. Refunds get processed according to policy. Identity checks follow security protocols."
  - CX teams write the logic in plain language, while engineers own "procedural thinking, setting up guardrails, and managing integrations."

  [mkt] — [Decagon blog, "Agent Operating Procedures: From Manual SOPs to Automated AI Logic" (n.d.)](https://decagon.ai/blog/from-sops-to-agent-operating-procedures)
- **Positioning and tooling.** "AOPs combine the power and flexibility of natural language with the precision and rigor of code." [mkt] — [Decagon blog, "Agent Operating Procedures: Decagon's approach to AI agents for CX" (n.d.)](https://decagon.ai/blog/aop-the-future-of-cx). Teams can "version with Git-based tracking," and the product shows "explanations of your agent's reasoning at any point in the conversation, with runtime and latency numbers." [mkt] — [Decagon AOP product page (accessed)](https://decagon.ai/product/aop)

#### Intercom Fin (Procedures plus the staged Fin AI Engine)
- **Procedures mix natural language with deterministic controls.** The controls are:
  - conditional steps (for example "whether a refund should be approved");
  - data connectors;
  - "code snippets for when absolute accuracy is essential and you need to guarantee that the same inputs always produce the same outputs";
  - checkpoints that pause for approval or hand off to a person.

  [mkt/docs] — [Intercom blog, "Announcing major updates to Procedures and Simulations" (n.d.)](https://www.intercom.com/blog/procedures-simulations-updates/); [Intercom, "What's new with Fin 3" (n.d.)](https://www.intercom.com/blog/whats-new-with-fin-3/)
- **Recommended build order and hard switches.** Intercom's advice is to start "with a fully natural language Procedure and introduc[e] structure gradually where it adds value." Explicit rules can force a switch to another procedure, for example "escalating to a complaints Procedure if specific risk signals are detected mid-conversation." [mkt/docs] — [Intercom blog, "Announcing major updates to Procedures and Simulations" (n.d.)](https://www.intercom.com/blog/procedures-simulations-updates/)
- **Starting a procedure is an LLM judgement.** "Fin evaluates every customer message to decide whether it matches a procedure's trigger description." Optionally, Fin "can automatically switch from the current procedure to another live procedure if it determines that the customer's intent has changed." [docs] — [Intercom Help, Building Fin Procedures (accessed)](https://www.intercom.com/help/en/articles/13449439-building-fin-procedures)
- **The Fin AI Engine runs as separate stages.**
  1. *Refine.* Rewrite the query, check "whether a Workflows automation or Custom Answer should be triggered," and safety-filter.
  2. *Retrieve and generate.* "If the output from the model doesn't meet the Fin AI Engine™ parameters for certainty, then a response is generated to ask the customer to clarify."
  3. *Validate.* Check that the answer addresses the query and "is grounded in the knowledge of your knowledge resources." If any safety check fails, "Fin will let the customer know that it cannot answer the query and escalate to human support."

  [docs] — [Intercom Help, "The Fin AI Engine" (accessed)](https://www.intercom.com/help/en/articles/9929230-the-fin-ai-engine)

#### Ada, Cognigy, Kore.ai, Voiceflow
- **Ada Playbooks.** "Every Playbook is built from six discrete step types... SEND, SET, ASK, RUN, IF/ELSE, GO TO." "For each step, you choose the mode — fixed or AI-driven — so you decide where the Agent follows your instructions exactly and where it reasons on its own." [docs] — [Ada docs changelog, Playbooks enhancements (URL dated 2026-06-22)](https://docs.ada.cx/2026-06-22-playbooks-enhancements)
- **Cognigy.** An LLM Entity Extract Node pulls entities "such as product codes, booking codes, or customer IDs" out of the input text inside an otherwise deterministic flow. The same LLM extraction is also built into the Question Node. [docs, search extract] — [Cognigy docs, LLM Entity Extract node](https://docs.cognigy.com/ai/agents/develop/node-reference/other-nodes/llm-entity-extract.md); [Cognigy v4.72 release blog](https://cognigy.com/blog/v4.72)
- **Kore.ai.** An Agent Node runs an LLM inside a dialog task for:
  - entity collection (the LLM "gathers entities conversationally");
  - tool calling;
  - streaming.

  Custom JavaScript runs "before and after each LLM interaction." Entity collection does not support custom, composite, list-of-items, or attachment entity types. [docs, search extract] — [Kore.ai docs, Agent Node](https://docs.kore.ai/ai-for-service/automation/agent-node)
- **Voiceflow.** Workflows are deterministic and are "used where the path must be fixed: authentication, IVR menus, scripted onboarding." Playbooks are agentic. The Playbook step lets a workflow "hand off to an intelligent, flexible conversation before returning to your deterministic logic." [docs, search extract] — [Voiceflow docs glossary](https://www.voiceflow.com/docs/resources/glossary/build); [Voiceflow Playbook step](https://www.voiceflow.com/docs/documentation/build/steps/playbook)

### Inferences
- **Three places the LLM tends to sit.**
  - *LLM as parser feeding a state machine*: Rasa commands, Lex Assisted NLU, Cognigy and Kore.ai entity extraction, Dialogflow flows. The LLM's output is a closed vocabulary that code validates and applies.
  - *LLM as orchestrator inside deterministic fences*: Agentforce's `available when` gates and before/after blocks, Bedrock return-of-control, Fin Procedure conditions and code, Ada fixed steps, Decagon AOPs. The LLM picks the next action, but only from the options code exposes.
  - *LLM with filtered context*: Parlant, and Sierra's supervisors. The LLM drives, but it sees only the rules relevant right now, and other models check its output.
- **Suggested mapping for the four-phase claims agent.**
  - *Identity verification*: a deterministic flow in which the LLM only extracts candidate values.
  - *Intent resolution*: a constrained command or classification step with an explicit "disambiguate" option, like Rasa's `disambiguate flows` or Fin's certainty-gated clarification.
  - *Case processing*: a procedure whose side-effecting tools stay hidden until their preconditions pass.
  - *Email summary*: a "generator"-style call (Google's term) fed from structured state, not from the raw transcript.
- **Who controls step order.** Once a vendor ships a "deterministic" tier, it no longer lets the LLM control step order for regulated steps. The LLM may *propose* a jump (Fin switching procedures, Parlant skipping journey states). In Agentforce, Rasa and FlowAgent-style designs, code then decides whether the jump is allowed.

### Gaps
- **Sierra "Journeys" are unverified.** Search extracts say Sierra's Journeys DSL compiles "deterministically and isomorphically" to Agent SDK code, and that cancelling an account or offering a promotion warrants higher determinism. Neither claim appeared on a page I could fetch. Exactly how supervisors veto or rewrite agent outputs is also unpublished.
- **Decagon, Ada and Fin internals are not documented.** Open questions include what "compile into validated workflows" means mechanically, how Ada's "AI-driven" step mode works, and how Fin scores trigger matches. These exist only as marketing or help-centre descriptions, with no engineering detail or numbers.
  - An Ada "Thinker/Talker" dual-model claim appears only on a third-party aggregator and was not verified.
- **Some vendor details are second-hand.** Kore.ai, Cognigy and Voiceflow details come from search extracts of their docs, not full fetches.
- **No independent benchmark compares these vendor architectures.**

## 2. What did teams try first (pure prompts, ReAct, function-calling agents), what failed, and why did they move to hybrid "flow engine + LLM" designs?

### Takeaway
The documented path swung from one extreme to the other before settling in the middle:
1. **Intent and decision-tree bots** were too brittle.
2. **Pure-prompt and ReAct/function-calling agents** were flexible but:
   - unpredictable from run to run;
   - prone to skipping or repeating steps;
   - prone to losing state and making up values;
   - expensive, at three to five or more LLM calls per turn.
3. **Hybrid designs (current)**: vendors put a deterministic skeleton back (state, gates, code steps) and kept LLMs for understanding and wording.

The clearest first-hand account is Salesforce's own list of how the old Agentforce failed. The clearest numbers are:
- **τ-bench:** function-calling agents succeeded on under 50% of tasks, and under 25% when the same task had to succeed 8 times in a row.
- **Rasa vs LangGraph:** a vendor-run comparison favouring Rasa.

### Cited Findings

#### Salesforce Agentforce: a first-party post-mortem of the LLM-only loop
- **What v1 was.** "In the previous version of Agentforce, an agent operated as a simple reactive loop. Every decision, from interpreting intent to selecting the next action, was made in real time by the LLM based solely on the user's most recent input. There was no guaranteed execution path, no persistent state, and no mechanism to enforce a sequence of steps." [docs] — [Salesforce Architects, Hybrid Reasoning (2026)](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- **Failures Salesforce lists.**
  - *Not reproducible*: "a workflow that passed in staging could behave differently in production, and no reliable way existed to reproduce a specific execution path."
  - *Expensive*: "Even simple conversational scenarios required a minimum of three LLM cycles: subagent selection, action selection, and final response generation. Multi-step tasks extended to five or more cycles."
  - *Not auditable*: "'the agent decided' is not a defensible answer in a compliance review."
  - *Lost context*: "If the conversation deviated even slightly, the agent could drop previously captured context and force the user to restart."
  - *Repeated steps*: "agents had no record of which mandatory steps a user had already completed. This produced unpredictable looping, where an agent would return to a step the user had already finished."

  [docs] — [Salesforce Architects, Hybrid Reasoning](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- **The fix Salesforce shipped.** Agent Script, Agent Graph, deterministic before/after blocks, and actions made available only when conditions hold, generally available since February 2026. On when to use it: "Deterministic logic is the right choice when the workflow requires a guaranteed execution sequence, when errors have significant consequences, when the process needs to produce a reproducible audit trail... Finance, healthcare, and insurance workflows generally fall into this category." [docs] — [Salesforce Architects, Hybrid Reasoning](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- **Engineering blog on the earlier prompt-heavy approach.**
  - "Reasoning through massive prompts" led to "goal drift, unpredictable outputs, and zero auditability."
  - Example: an agent guiding a user through an inquiry form, asked "How's the weather in Austin?", "can lose focus on the goal of completing the form."
  - It also warns against the other extreme: "Simply adding if/else rules or 'flows' as agentic actions can devolve quickly into unmanageable, interdependent logic."

  [eng] — [Phil Mui, Salesforce Engineering (Oct 20; year not printed)](https://engineering.salesforce.com/agentforces-agent-graph-toward-guided-determinism-with-hybrid-reasoning/)

#### Rasa's vendor benchmark: a ReAct-style LangGraph agent vs CALM
- **Setup.** GPT-4 for both systems, on an airline/travel assistant derived from LangGraph's own customer-support tutorial. Five conversations were run with identical user prompts. [bench] — [Rasa blog, "Cutting AI Assistant Costs by Up to 77.8%", 2024-08-08](https://rasa.com/blog/cutting-ai-assistant-costs-the-power-of-enhancing-llms-with-business)
- **Results.**

  | | LangGraph | CALM | CALM with NLU in front |
  |---|---|---|---|
  | Mean cost per user message | $0.10 | $0.04 | — |
  | Mean latency per user message | 7.4 s | 2.08 s | 1.58 s |
  | Cost, user changes their mind | $0.18 | $0.04 | — |
  | Latency, user changes their mind | 10.1 s | 1.72 s | 1.41 s |

  - The headline claim is "up to 77.8%" cost reduction.
  - LangGraph produced "only 1 problem-free conversation out of 5."
  - LangGraph failures included "asking for the ID instead of the name," impossible hotel date ranges ("April 22 - April 20"), choosing the wrong flight, and inconsistently formatted confirmations.
  - The CALM conversations "will move through the business logic... and will differ only in the type of chit-chat and rephrasing."

  [bench] — [Rasa blog, "Cutting AI Assistant Costs by Up to 77.8%", 2024-08-08](https://rasa.com/blog/cutting-ai-assistant-costs-the-power-of-enhancing-llms-with-business)
- **Caveats.** This is a vendor-run comparison with five conversations in one domain, although the code is published for reproduction. [bench] — [Rasa blog, "Cutting AI Assistant Costs by Up to 77.8%", 2024-08-08](https://rasa.com/blog/cutting-ai-assistant-costs-the-power-of-enhancing-llms-with-business)

#### τ-bench (function-calling agents that must follow a policy)
- **Headline result.** "Even state-of-the-art function calling agents (like gpt-4o) succeed on <50% of the tasks, and are quite inconsistent (pass^8 <25% in retail)." (pass^k is the share of tasks an agent gets right on all k repeated tries.) The authors call for "methods that can improve the ability of agents to act consistently and follow rules reliably." [paper] — [Yao, Shinn, Razavi, Narasimhan, "τ-bench", arXiv 2406.12045, 2024-06-17](https://arxiv.org/abs/2406.12045)
- **Setup.** An LLM simulates the user. The agent gets domain API tools and policy guidelines. Grading compares the final database state with an annotated goal state. [paper] — [Yao, Shinn, Razavi, Narasimhan, "τ-bench", arXiv 2406.12045, 2024-06-17](https://arxiv.org/abs/2406.12045)

#### FlowAgent (academic work on the compliance vs flexibility trade-off)
- **The trade-off.** "Traditional rule-based methods tend to limit the inherent flexibility of LLMs, as their predefined execution paths restrict the models' action space." Prompt-based methods let the LLM control the flow, but at the cost of weaker procedural compliance. [paper] — [Shi et al. (Tencent YouTu Lab / Fudan), "FlowAgent: Achieving Compliance and Flexibility for Workflow Agents", arXiv 2502.14345, 2025-02-20](https://arxiv.org/abs/2502.14345)
- **The design has three parts.**
  - A Procedure Description Language that mixes natural language and pseudocode, with preconditions on each node.
  - Pre-decision controllers that give soft guidance before the LLM acts.
  - Post-decision controllers that hard-validate the LLM's chosen action.

  [paper] — [Shi et al., "FlowAgent: Achieving Compliance and Flexibility for Workflow Agents", arXiv 2502.14345, 2025-02-20](https://arxiv.org/abs/2502.14345)
- **Results with GPT-4o (whole-conversation success rate).**

  | Dataset | FlowAgent | ReAct-style baselines | FlowAgent, off-workflow tests |
  |---|---|---|---|
  | In-house | 67.72% | roughly 40–62% | 57.26% |
  | STAR | 42.78% | 33–40% | 22.22% |
  | SGD | 32.79% | 29–34% | 16.67% |

  - The last column covers conversations where the user goes "out of workflow" (asks off-script questions or tries to skip steps). ReAct degraded more steeply under these tests.
  - An ablation found the controllers "indispensable."

  [paper; numbers taken via a summary of the HTML version] — [Shi et al., "FlowAgent: Achieving Compliance and Flexibility for Workflow Agents", arXiv 2502.14345, 2025-02-20](https://arxiv.org/abs/2502.14345)

#### Early research on LLM-only task-oriented dialogue
- LLMs "underperform" specialised models "in explicit belief state tracking" (i.e. keeping track of slot values). However, they "show some ability to guide the dialogue to a successful ending through their generated responses if provided with correct slot values." [paper] — [Hudeček & Dušek, "Are Large Language Models All You Need for Task-Oriented Dialogue?", SIGDIAL 2023, arXiv 2304.06556](https://arxiv.org/abs/2304.06556)

#### Parlant's ARQ paper: why plain prompts and chain-of-thought fell short
- ARQs ("attentive reasoning queries": structured, domain-specific reasoning questions the model answers before replying) "achieved a 90.2% success rate across 87 test scenarios, outperforming both Chain-of-Thought reasoning (86.1%) and direct response generation (81.5%)." The gain was largest on two failure modes: re-applying a guideline that had already been addressed, and hallucination. [paper, vendor-authored] — [Karov, Zohar, Marcovitz, "Attentive Reasoning Queries", arXiv 2503.03669, 2025-03-05](https://arxiv.org/abs/2503.03669)

#### Microsoft Copilot Studio: regressions users reported with generative orchestration
- **User reports.**
  - With generative orchestration on, a topic "repeats the same question multiple times and never proceeds to the next step," even after a valid answer. Turning generative orchestration off made "the exact same question and user input" work.
  - A second user: with "Use generative AI" on, "it asks the questions for information that is provided in the initial prompt." With it off, the bot "correctly slot fills the information that is already present."
- **Microsoft's forum reply.** A Microsoft Q&A responder called this "expected—when 'Use generative AI' is enabled, the system prioritizes conversational flow and may re-ask for details even if they're present... You can consider turning off generative AI for more deterministic slot behavior."

[forum] — [Microsoft Q&A thread, 2025-04-15 to 2025-09-01](https://learn.microsoft.com/en-us/answers/questions/2258167/generative-ai-orchestration-repeats-questions-and)
- **Official docs confirm the memory limit.** "The amount of conversation history is currently limited, which means that sometimes the agent can't see or use the information in earlier parts of the conversation. In these cases, it might be necessary to collect some information again from the user, or ensure that key information is included in the transcript at regular intervals." [docs] — [Microsoft Learn, generative orchestration known limitations](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-generative-actions)

#### Klarna: the best-known "AI-first support" case, and its partial reversal
- **Launch numbers (February 27, 2024; OpenAI-powered assistant).**
  - 2.3 million conversations in the first month, two-thirds of all chats.
  - "Equivalent work of 700 full-time agents."
  - Customer satisfaction "on par" with human agents.
  - Errands resolved in under 2 minutes instead of 11.
  - 25% fewer repeat inquiries.
  - An estimated $40M profit improvement in 2024.

  [press, reprint] — [VKTR reprint of Klarna press release, 2024-02-27](https://www.vktr.com/the-wire/klarna-ai-assistant-handles-two-thirds-of-customer-service-chats-in-its-first-month)
- **Rebuild on LangGraph.** LangChain's case study says the assistant was built on LangGraph and LangSmith as "a controllable agent architecture" that routed requests. It also "dynamically tailor[ed] prompts to specific scenarios," which reduced token costs and latency. [vendor case study, search extract] — [LangChain, "How Klarna's AI assistant redefined customer support" (n.d.)](https://www.langchain.com/blog/customers-klarna)
- **May 2025 reversal.** CEO Sebastian Siemiatkowski: "As cost unfortunately seems to have been a too predominant evaluation factor when organising this, what you end up having is lower quality." Klarna began hiring human agents again in an "Uber type of setup." [press, secondary report of a Bloomberg interview] — [CX Today, "Klarna Grapples with AI-Led Customer Service, Pivots to an 'Uber Type of Setup'" (2025)](https://cxtoday.com/klarna-grapples-with-ai-led-customer-service-pivots-to-an-uber-type-of-setup)

#### Sierra: from "perfect adherence" to bounded error rates
- "At scale, a one-in-ten-thousand hallucination rate is a daily occurrence." Sierra's response is layered supervisor agents that combine "fast, high recall detection with slower, high precision reasoning so the system automatically surfaces and logs defects." [eng] — [Sierra, "From LLMs to enterprise-grade agents", 2025-10-02](https://sierra.ai/blog/enterprise-grade-agents)

#### Decagon and Intercom: moving away from decision trees and visual flow builders
- **Decagon.** "Rather than codifying every possible scenario into an inflexible tree structure, AOPs allow you to articulate your business logic in a way that the AI can interpret and adapt." The company describes the shift as "moving away from brittle decision trees" toward natural language "reinforced with robust guardrails." [mkt, search extract] — [Decagon, "Why Decagon built Agent Operating Procedures" (n.d.)](https://decagon.ai/blog/why-we-built-aop)
- **Intercom.** Procedures ("natural language + deterministic controls") are positioned against the older Workflows, which use a "visual drag-and-drop builder." [mkt, search extract] — [fin.ai, "Fin Procedures: Multi-Step AI Workflow Guide" (n.d.)](https://fin.ai/learn/fin-procedures-guide)

#### OpenAI's own caveat
- "Before committing to building an agent, validate that your use case can meet these criteria clearly. Otherwise, a deterministic solution may suffice." [docs] — [OpenAI, "A practical guide to building agents" (2025; PDF undated)](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)

### Inferences
- **The failure modes that drove the hybrid shift map directly onto the claims-agent requirements.**

  | Observed failure | Implication for the claims agent |
  |---|---|
  | Skipped or out-of-order steps | Identity must be a hard precondition, not an instruction. |
  | Lost context and repeated questions | Facts the user volunteers must live in explicit state, not only in the transcript. |
  | Looping on steps already done | Each step needs a completion flag in state. |
  | Hallucinated or inconsistent values | Extraction must be schema-validated, with validation repeated in code. |
  | Three to five or more LLM calls per turn | Collapse understanding into one constrained call and keep transitions in code. |

- **The two camps met in the middle.** LLM-first vendors (Salesforce, Microsoft, Intercom, Decagon) added determinism. Deterministic vendors (Rasa, Google, AWS, Cognigy, Kore.ai, Voiceflow) added LLM parsing.
- **Klarna's lesson.** Even an agent with strong launch metrics can disappoint on quality and empathy when it is tuned mainly for cost. Escalation to humans and quality metrics (not only containment) belong in the design from the start.

### Gaps
- **The evidence is mostly from vendors.** Salesforce, Rasa, Parlant and Sierra all have a product to position. The Rasa benchmark covers five conversations in one domain. I found no independent production post-mortem that reports step-skip or hallucinated-slot rates.
- **Klarna's details are not public.** Its post-reversal architecture changes and failure metrics are unpublished; only CEO quotes in secondary press are available.
- **No Salesforce numbers.** Salesforce has not published before/after figures for Agent Script on latency, cost or policy adherence.

## 3. Patterns for multi-turn slot filling and dialogue state tracking with LLMs (over-answering, digressions, corrections, confirmation, schema-validated extraction, protecting verified values)

### Takeaway
Production systems treat the LLM as a *proposer* of state updates and keep the state store, write permissions and validation in code.
- **Over-answering:** the LLM may capture any slot it hears, at any turn.
- **Security-sensitive slots** (identity, "verified" flags) can be written only by deterministic code or tools.
- **Corrections** are explicit commands handled by a repair pattern, optionally with a confirmation question.
- **Digressions** pause and later resume the current flow instead of abandoning it.
- **Gate variables** that unlock later phases are never set by the LLM.

### Cited Findings

#### Capturing over-answers without advancing the process
- **Rasa.**
  - One user message can produce several commands, e.g. `[SetSlot(...), StartFlow(...)]`.
  - By default (`from_llm` mapping), the LLM can fill slots "at any conversation point, not just during their designated collect step."
  - Since 3.12, if "the user is prompted to fill a slot and responds with info to fill both the requested slot and other slots... the other slots can now be captured by LLM-based command generators."

  [docs] — [Rasa docs, LLM Command Generators](https://rasa.com/docs/reference/config/components/llm-command-generators/); [Rasa docs, Slots](https://rasa.com/docs/reference/primitives/slots/)
- **Copilot Studio.** Generative orchestration pre-fills tool and topic inputs. In Microsoft's examples it takes "Seattle" from the current message and "Kirkland" from earlier context. The documented history limit (see section 2) caps how far back this works. [docs] — [Microsoft Learn, generative orchestration](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-generative-actions)
- **Parlant journeys.** The agent "may jump multiple states (if the conditions for doing so apply), revisit previous ones." The 3.0 release describes "Flexible State Transitions: ... skip states, revisit previous states, or jump ahead based on context and user needs." [docs/mkt] — [Parlant docs, Journeys](https://www.parlant.io/docs/concepts/customization/journeys); [Parlant 3.0 blog, 2025-08-15](https://parlant.io/blog/parlant-3-0-release/)
- **Fin Procedures.** Fin can move "non-linearly across steps to match the natural flow of real customer conversations, revisiting or skipping steps as needed." [docs] — [Intercom Help, Fin Procedures explained](https://www.intercom.com/help/en/articles/12495167-fin-procedures-explained)

#### Stopping the agent from skipping ahead
- **Agentforce: conditional action availability.**
  - "When the `available when` condition evaluates to `false`, the action is removed from the tool list presented to the LLM entirely... This is not a prompt instruction telling the LLM 'don't call this yet,' it's a hard platform-level gate. The LLM can't call an action it can't access."
  - Rule: "never let the LLM set the gate variable... If you rely on the LLM to set the gate variable, you've reintroduced the variability the gate was designed to prevent."
  - Gates start closed: the "`validation_passed` boolean [defaults] to `false`... The workflow has to actively earn the right to proceed at each step."
  - Action parameters come from validated session variables: "The LLM isn't assembling the parameters from conversation context; it's reading them from the state."
  - Because state persists, "the conversation can deviate, the user can ask follow-up questions, and the agent will still know exactly where it is in the process."

  [docs] — [Salesforce Architects, Hybrid Reasoning](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- **Agentforce pitfalls.**
  - *Action loops*: if the gate stays open after the action runs and the instructions are ambiguous, "the LLM calls the same action on every parse indefinitely." The fixes are to close the gate after execution, or to add a `has_run` boolean.
  - *Skipped clean-up*: setting `is_displayable: True` on an action exits the reasoning loop, so `after_reasoning` never runs. Logic that must always run should go in the *next* subagent's `before_reasoning` block.

  [docs] — [Salesforce Architects, Hybrid Reasoning](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- **FlowAgent.** It names three kinds of "out-of-workflow" behaviour: intent switching (changing parameters or cancelling), procedure jumping (skipping sequential steps), and irrelevant answering. Two controllers handle them:
  - *Pre-decision controllers (soft)*: they flag "inaccessible nodes by examining the dependency graph."
  - *Post-decision controllers (hard)*: they block, for example, a transition "to query_appointment without completing check_department."

  [paper] — [FlowAgent, arXiv 2502.14345, 2025-02-20](https://arxiv.org/abs/2502.14345)
- **Parlant dependencies.** A guideline can depend on others, e.g. `compliance_guideline.depend_on(identity_check, account_lookup)`. [docs] — [Parlant 3.2 blog](https://parlant.io/blog/parlant-3-2-streaming-responses/)

#### Write-protecting verified values
- **Rasa `controlled` slots.** These are slots "filled by a custom action, response button payload, or a `set_slots` flow step." A slot with only this mapping is not available to NLU or the LLM. [docs] — [Rasa docs, Slots](https://rasa.com/docs/reference/primitives/slots/)
- **Rasa `allow_nlu_correction`.** By default (3.12 and later), "the LLM-based command generator is not allowed to correct slots that have been filled by the NLU-based pipeline" unless this is set to true. When the two conflict, the NLU-issued `SetSlot` wins. [docs] — [Rasa docs, Slots](https://rasa.com/docs/reference/primitives/slots/); [Rasa docs, LLM Command Generators](https://rasa.com/docs/reference/config/components/llm-command-generators/)
- **Rasa validation before acceptance.** Slot validation/rejection rules (pypred expressions) run before an LLM-extracted value is accepted. `ask_before_filling` on a collect step is documented as a guard against greedy pre-filling; its exact wording was seen only via the fetch summary. [docs] — [Rasa docs, Slots](https://rasa.com/docs/reference/primitives/slots/)
- **Lex.** Assisted NLU stays "within your bot's configured intents and slots." Assisted slot resolution is limited to specific built-in types (Date, Number, PhoneNumber, Alphanumeric without regex, and others). [docs] — [AWS docs, Assisted NLU](https://docs.aws.amazon.com/lexv2/latest/dg/assisted-nlu.html)
- **Copilot Studio.** Regex and closed-list entities can't be filled by generative orchestration; Microsoft says to "use a Question node" instead. [docs] — [Microsoft Learn, generative orchestration](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-generative-actions)

#### Corrections and confirmation
- **Rasa.** The command prompt says `set slot` "can be used to correct and change previously set values." Corrections are handled by `pattern_correction`, whose default "simply updates the value." The docs show how to customise it to ask "Do you want to update the [slot name]?" before accepting the change. [docs] — [Rasa docs, Patterns](https://rasa.com/docs/reference/primitives/patterns/)
- **Bedrock Agents.** You can require user confirmation before an action runs. Return-of-control lets the application inspect and validate the elicited parameters before anything executes. [docs] — [AWS Bedrock docs, Return control](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-returncontrol.html)
- **OpenAI guide.**
  - High-risk actions ("canceling user orders, authorizing large refunds, or making payments") "should trigger human oversight until confidence in the agent's reliability grows."
  - Tools should be risk-rated (read-only vs write access, reversibility, account permissions, financial impact), and those ratings used to pause for checks.

  [docs] — [OpenAI, "A practical guide to building agents"](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)

#### Digressions
- **Rasa.**
  - `pattern_continue_interrupted` handles users switching between flows, letting them resume or cancel the interrupted one.
  - `pattern_chitchat` handles "off-topic interactions that won't disrupt the main conversation."
  - `search and reply` handles knowledge questions.
  - The prompt also says: "Multiple flows can be started without cancelling the previous."

  [docs] — [Rasa docs, Patterns](https://rasa.com/docs/reference/primitives/patterns/); [Rasa docs, LLM Command Generators](https://rasa.com/docs/reference/config/components/llm-command-generators/)
- **Fin.** It switches procedures deterministically on risk signals, and automatically when the customer's intent changes. [docs] — [Intercom blog, Procedures updates](https://www.intercom.com/blog/procedures-simulations-updates/); [Intercom Help, Building Fin Procedures](https://www.intercom.com/help/en/articles/13449439-building-fin-procedures)

#### Schema-validated extraction
- **Rasa extraction rules in the prompt.**
  - "Do not fill slots with abstract values or placeholders."
  - "For categorical slots try to match the user message with allowed slot values. Use 'other' if you cannot match it."
  - "Extract text slot values exactly as provided by the user. Avoid assumptions, format changes, or partial extractions."

  [docs] — [Rasa docs, LLM Command Generators](https://rasa.com/docs/reference/config/components/llm-command-generators/)
- **FnCTOD.** Treats each domain's schema as a function and dialogue state tracking as "calling" that function. It improves ChatGPT's average joint goal accuracy by 5.6% (GPT-3.5 +4.8%, GPT-4 +14%) and lets 7B/13B models beat the earlier ChatGPT state of the art. [paper] — [Li et al., "Large Language Models as Zero-shot Dialogue State Tracker through Function Calling", ACL 2024, arXiv 2402.10466](https://arxiv.org/abs/2402.10466)
- **CorrectionLM.** A second, self-correcting pass by a small model over the predicted dialogue state adds +16.1 and +21.3 points of joint goal accuracy on MultiWOZ and SGD, compared with a single pass. [paper, search extract] — [CorrectionLM, arXiv 2410.18209 (Oct 2024)](https://arxiv.org/abs/2410.18209)
- **OpenAI Structured Outputs.** Strict JSON Schema mode scored 100% on complex schema following, against under 40% for gpt-4-0613. But it "doesn't prevent all kinds of model mistakes... the model may still make mistakes within the values of the JSON object." [eng] — [OpenAI, "Introducing Structured Outputs in the API", 2024-08-06](https://openai.com/index/introducing-structured-outputs-in-the-api/)
- **Pydantic AI.**
  - Validation failures on tool arguments or structured output are sent back to the model as retry prompts ("the call didn't work, here is why, try again").
  - Output validators can raise `ModelRetry`.
  - The default retry budget is 1.
  - `UnexpectedModelBehavior` is raised "when the budget runs out."

  [docs] — [Pydantic AI docs, Retries](https://pydantic.dev/docs/ai/core-concepts/retries/index.md)

### Inferences
- **Remembering without skipping ahead.** Suppose the user says "my denied healthcare claim from January" during identity verification.
  - Run one extraction or command call every turn against the slot schema of *all* phases, Rasa-style.
  - Write any value that belongs to a later phase into state tagged `provisional`, with where it came from (turn id, raw text span). This covers "remember."
  - The phase controller (code) and tool gating (Agentforce-style `available when`) stop the agent from skipping ahead.
  - On entering intent resolution, pre-fill from the provisional values with an implicit confirmation ("You mentioned a claim from January that was denied — is that the one?"). This avoids Copilot Studio's documented "re-asks for information already given" failure.
- **Identity fields.**
  - Extraction may *propose* a member ID or date of birth.
  - Only the verification tool writes them into the verified store (like Rasa's `controlled` slots) and sets `identity_verified`.
  - A later "actually my date of birth is..." should go through a correction path that cancels the verification and runs it again, rather than silently overwriting the verified value.
- **Do not use the transcript as memory for volunteered facts.** Copilot Studio documents that history gets truncated, and Salesforce documents that "state didn't survive the conversation."

### Gaps
- **Rasa flow guards unverified.** I did not verify Rasa's "flow guards" (conditions that stop a flow from starting) in this session. They would directly enforce "don't start the claims flow before authentication."
- **No published error rates.** No vendor reports error rates for capturing over-answers, handling corrections, or confirmation strategies. No source measured the trade-off between implicit and explicit confirmation for LLM agents.
- **Benchmarks ignore provenance.** Academic dialogue-state benchmarks (MultiWOZ, SGD) don't model where a value came from ("verified" vs "volunteered") or security-gated slots. I found no paper on accidental overwriting of verified values.

## 4. Patterns for a "freedom dial": strict scripted steps vs freer grounded steps, constrained generation, separate extractor and responder vs one call with tools, structured outputs, natural tone over scripted content

### Takeaway
- **The dial is set per step or node.** Every vendor exposes a per-step setting for how much freedom the LLM gets:
  - Ada: fixed vs AI-driven;
  - Agentforce: deterministic vs prompt instructions;
  - Google: flows vs generators vs playbooks;
  - Voiceflow: workflows vs playbooks;
  - Parlant: composition mode per guideline;
  - Fin: natural-language steps with optional code.
- **Content is fixed by templates; the LLM handles tone.** Code templates or selects the substance (canned responses with tool-filled fields, or a template with optional rephrasing). The LLM only adjusts tone.
- **Work is split into separate calls or models.** Understanding, acting, validating and phrasing run separately.

### Cited Findings

#### Step-level dials
- **Ada.** Each step is set to "fixed or AI-driven." [docs] — [Ada docs, Playbooks enhancements](https://docs.ada.cx/2026-06-22-playbooks-enhancements)
- **Agentforce.** Each node is either deterministic logic or a prompt instruction, and "the default position should be deterministic."

  | Use deterministic logic for | Use LLM reasoning for |
  |---|---|
  | Input validation and sanitization | Natural language understanding and intent detection |
  | Business rule enforcement | Generating conversational, empathetic responses |
  | Sequential process orchestration | Handling ambiguous or unexpected inputs |
  | State management and context preservation | Providing explanations and clarifications |
  | Guard clauses preventing invalid operations | Adapting tone and messaging to user context |

  In Salesforce's bank-transfer example, a deterministic rule catches a transfer-limit breach and sets `validation_passed=false`. The LLM then "present[s] the user with three options" conversationally. Error messages are assembled "from session variables rather than generated by the LLM... The LLM's role here is purely presentational." [docs] — [Salesforce Architects, Hybrid Reasoning](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- **Google.** The tiers run from deterministic flows (static responses), to partly generative (Generators, Generative fallback), to fully generative (Playbooks). [docs] — [Google Cloud docs, Generative versus deterministic](https://docs.cloud.google.com/dialogflow/cx/docs/generative-deterministic)
- **Sierra.**
  - "Define the degree of flexibility your agent should exhibit for each workflow." [mkt] — [Sierra Agent SDK](https://sierra.ai/product/agent-sdk)
  - Tasks given more agency are backed by supervisors. [eng] — [Sierra, Constellation of models](https://sierra.ai/blog/constellation-of-models)
- **Fin.** Start fully in natural language and add conditions, data connectors and code snippets "where precision matters." [mkt] — [Intercom blog, Procedures updates](https://www.intercom.com/blog/procedures-simulations-updates/)
- **Voiceflow.** Deterministic workflows can call agentic playbooks and then return to the deterministic logic. [docs, search extract] — [Voiceflow Playbook step](https://www.voiceflow.com/docs/documentation/build/steps/playbook)

#### Controlling what replies say
- **Parlant composition modes.**

  | Mode | What it does |
  |---|---|
  | FLUID | Free LLM generation, structured with ARQs. |
  | STRICT | The agent may only choose a pre-written canned response, e.g. "Your account balance is {balance}." or "I am not authorized to provide financial advice." |
  | COMPOSITED | Mixes canned and generated text. |
  | CANNED_FLUID | Generated text that imitates the style of canned examples. |

  [docs] — [Parlant docs, Message Generation](https://www.parlant.io/docs/engine-internals/message-generation/)
- **Parlant per-guideline controls (3.1).**
  - A guideline can override the agent's mode. For off-topic chat, for example, it can force `composition_mode=STRICT` with the canned reply "I'm here to help with your account. Could we focus on that?"
  - Canned responses can be scoped to a guideline or a journey state.
  - Placeholder fields are filled by tools or guidelines ("Here's your discount code: {{discount_code}}").
  - Guidelines carry a HIGH or LOW criticality.

  [docs] — [Parlant 3.1 blog](https://parlant.io/blog/parlant-3-1-release/)
- **Parlant's claim about canned responses.** They are said to "[eliminate] hallucination risk entirely" at critical moments. [mkt] — [Parlant README](https://github.com/emcie-co/parlant)
- **Parlant's ARQ output shape.** For each matched guideline the model fills in `guideline_content`, `how_to_address` and `addressed_in_response`, and only then writes the `message`. [docs] — [Parlant docs, Message Generation](https://www.parlant.io/docs/engine-internals/message-generation/)
- **Rasa rephrasing.** Replies are templates first, with optional per-response LLM rephrasing. Rasa warns: "Sometimes, the LLM will not generate a true paraphrase, but slightly alter the meaning." The default temperature is 0.3, and the rephraser sees a summarised conversation history (produced by additional LLM calls). [docs] — [Rasa docs, Contextual Response Rephraser](https://rasa.com/docs/reference/primitives/contextual-response-rephraser/)
- **OpenAI guide.** When writing routines, "being explicit about the action (and even the wording of a user-facing message) leaves less room for errors in interpretation." [docs] — [OpenAI, "A practical guide to building agents"](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- **Copilot Studio.** Returning topic outputs to the orchestrator yields "contextual responses" instead of fixed Message-node text. The trade-off is more natural replies but less control over their content. [docs] — [Microsoft Learn, generative orchestration](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-generative-actions)

#### Splitting work across calls and models
- **Anthropic.**
  - Run guardrails as a separate, parallel call: "one model instance processes user queries while another screens them for inappropriate content or requests. This tends to perform better than having the same LLM call handle both guardrails and the core response."
  - Prompt chaining uses programmatic "gates" between steps.
  - Routing gives "separation of concerns."

  [docs] — [Anthropic, "Building effective agents", 2024-12-19](https://www.anthropic.com/engineering/building-effective-agents)
- **Rasa.** By design, each turn goes through separate stages: understanding (command generator), then deterministic flows and policies, then a template (plus the optional rephraser LLM). [docs] — [Rasa docs, LLM Command Generators](https://rasa.com/docs/reference/config/components/llm-command-generators/)
- **Fin.** Refine, generate and validate are separate stages, with a clarifying question when certainty is below threshold. [docs] — [Intercom Help, Fin AI Engine](https://www.intercom.com/help/en/articles/9929230-the-fin-ai-engine)
- **Sierra.** Different models handle different task types: low-latency tool calling, classification, long-context reasoning, and tone. [eng] — [Sierra, Constellation of models](https://sierra.ai/blog/constellation-of-models)
- **Talker–Reasoner (DeepMind research).**
  - A fast "Talker" handles conversation.
  - A slow "Reasoner" handles multi-step reasoning and tool calls and produces "the new agent state."
  - The Talker can say "conversationally contingent phrases to hide Reasoner latency."

  [paper, search extract] — [Christakopoulou et al., "Agents Thinking Fast and Slow: A Talker-Reasoner Architecture", arXiv 2410.08328, Oct 2024](https://arxiv.org/abs/2410.08328)
- **OpenAI Agents SDK.** An input guardrail can run on "a fast/cheap model" in parallel with the main agent. [docs] — [OpenAI Agents SDK, Guardrails](https://openai.github.io/openai-agents-python/guardrails/)

#### Structured outputs
- **OpenAI Structured Outputs.**
  - Schema adherence was 100%, against under 40% before.
  - The first request with a new schema is slow: "Typical schemas take under 10 seconds to process on the first request, but more complex schemas may take up to a minute."
  - A refusal comes back in a `refusal` field.
  - The output can still break the schema if generation hits `max_tokens`.

  [eng] — [OpenAI, Structured Outputs, 2024-08-06](https://openai.com/index/introducing-structured-outputs-in-the-api/)
- **Agentforce.** "Structured output generation... where the response needs to conform to a defined schema" is one of its eight LLM invocation points. [docs] — [Salesforce Architects, Hybrid Reasoning](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- **Rasa.** The output is constrained to a line-based action language ("Strictly adhere to the provided action format") that code parses. [docs] — [Rasa docs, LLM Command Generators](https://rasa.com/docs/reference/config/components/llm-command-generators/)

### Inferences
- **Mapping the claims phases onto the dial.**
  - *Identity verification*: STRICT. Canned prompts, validation in code; the LLM only extracts.
  - *Intent resolution*: constrained command generation, with COMPOSITED or CANNED_FLUID replies.
  - *Case processing*: a deterministic tool sequence, plus a free but grounded Q&A sub-mode (like Fin, or Rasa's `search and reply`) that is checked for groundedness.
  - *Email summary*: a template with LLM-written fields drawn only from structured state.
- **"Reply briefly" should be enforced structurally**, through a canned response with placeholders or a length-limited field in a JSON schema, rather than by instruction.
- **Check rephrased text.** Rasa's own warning about meaning drift implies a post-check that numbers, dates and claim IDs in a rephrased reply match those in the template.
- **Extractor vs single call.** Separate calls are the dominant production pattern: an extractor (small model, structured output, temperature 0) and a responder (tone). A single tool-calling call per turn is cheaper, but it recreates Agentforce v1's problems unless code controls which tools are exposed.

### Gaps
- **Rephraser drift is unmeasured.** No vendor publishes rates of meaning drift for its rephraser or tone layer.
- **No head-to-head data.** There is no public comparison of "one tool-calling call per turn" against "extractor plus responder" on cost, latency and adherence. The only evidence is the vendor-run Rasa comparison and Agentforce's qualitative "three to five LLM cycles" statement.
- **Undocumented internals.** How Ada's "AI-driven" step mode works, and how Sierra's tone model and supervisors interact, are not publicly documented.

## 5. What do Anthropic, OpenAI, LangGraph/LangChain, Pydantic AI, Microsoft and Google design docs say about workflow-bound agents?

### Takeaway
First-party guidance from all of these is consistent:
- Use predefined code paths (workflows) where the process is known, and save agent autonomy for the open-ended parts.
- Put programmatic gates between steps.
- Use smaller models wherever evaluations show they are good enough.
- Layer guardrails, including deterministic rules.
- Escalate to a human when failure thresholds are exceeded or an action is high-risk.

Frameworks now ship explicit workflow or graph primitives alongside agents: ADK workflow agents, Microsoft Agent Framework workflows, and LangGraph.

### Cited Findings

#### Anthropic, "Building effective agents" (December 19, 2024)
- **Definitions.** "Workflows are systems where LLMs and tools are orchestrated through predefined code paths. Agents, on the other hand, are systems where LLMs dynamically direct their own processes and tool usage." [docs] — [Anthropic, Building effective agents, 2024-12-19](https://www.anthropic.com/engineering/building-effective-agents)
- **Trade-offs.**
  - "Agentic systems often trade latency and cost for better task performance, and you should consider when this tradeoff makes sense."
  - "Workflows offer predictability and consistency for well-defined tasks, whereas agents are the better option when flexibility and model-driven decision-making are needed at scale."
  - "Start with simple prompts, optimize them with comprehensive evaluation, and add multi-step agentic systems only when simpler solutions fall short."

  [docs] — [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- **Patterns.**
  - Prompt chaining: "You can add programmatic checks (see 'gate' in the diagram below) on any intermediate steps to ensure that the process is still on track."
  - Routing.
  - Parallelization (splitting work into sections, or voting), including the guardrail example.
  - Orchestrator-workers.
  - Evaluator-optimizer.

  [docs] — [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- **Customer-support appendix.**
  - Support "naturally follow[s] a conversation flow while requiring access to external information and actions."
  - "Actions such as issuing refunds or updating tickets can be handled programmatically."
  - "Success can be clearly measured through user-defined resolutions."

  [docs] — [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- **Risks.** "The autonomous nature of agents means higher costs, and the potential for compounding errors. We recommend extensive testing in sandboxed environments, along with the appropriate guardrails." Tool design (the "agent-computer interface") deserves as much effort as user-interface design, including "poka-yoke" (mistake-proof) tools. [docs] — [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)

#### OpenAI, "A practical guide to building agents" (2025; PDF undated), plus the Agents SDK
- **When to use an agent.** Agents fit "workflows where traditional deterministic and rule-based approaches fall short." The guide's example of heavy reliance on unstructured data is "processing a home insurance claim." Otherwise, "a deterministic solution may suffice." [docs] — [OpenAI practical guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- **Writing instructions.**
  - "Use existing operating procedures, support scripts, or policy documents to create LLM-friendly routines."
  - "Providing smaller, clearer steps from dense resources helps minimize ambiguity."
  - "Make sure every step in your routine corresponds to a specific action or output."
  - Anticipate common variations "with conditional steps or branches such as an alternative step if a required piece of info is missing."

  [docs] — [OpenAI practical guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- **Choosing models.** Prototype with the most capable model to set a baseline, then "try swapping in smaller models to see if they still achieve acceptable results." In the guide's words: "a simple retrieval or intent classification task may be handled by a smaller, faster model, while harder tasks like deciding whether to approve a refund may benefit from a more capable model." The order of priorities is: set up evals, meet the accuracy target, then optimise cost and latency. [docs] — [OpenAI practical guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- **When to split into multiple agents.**
  - When prompts contain "many conditional statements (multiple if-then-else branches)."
  - When tools overlap. The issue is similarity, not count: "Some implementations successfully manage more than 15 well-defined, distinct tools while others struggle with fewer than 10 overlapping tools."
  - Two patterns are offered: a manager agent that calls others as tools, or decentralised handoffs between agents ("a handoff is a type of tool").

  [docs] — [OpenAI practical guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- **Guardrails as layered defence.** The guide lists seven types:

  | Guardrail | What it does |
  |---|---|
  | Relevance classifier | Keeps the agent on topic. |
  | Safety classifier | Catches jailbreaks and prompt injection. |
  | PII filter | Stops personal data leaking in outputs. |
  | Moderation | Flags harmful or inappropriate inputs. |
  | Tool safeguards | Rates each tool's risk (low/medium/high) by read vs write access, reversibility, permissions and financial impact. |
  | Rules-based protections | Blocklists, input length limits, regex filters. |
  | Output validation | Checks responses against brand and content rules. |

  Escalate to a human on "exceeding failure thresholds" (e.g. the agent "fails to understand customer intent after multiple attempts") and on "high-risk actions." [docs] — [OpenAI practical guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- **Agents SDK guardrails.**
  - Input guardrails "run only for the first agent in the chain."
  - Output guardrails run "only for the agent that produces the final output."
  - *Parallel mode* "provides the best latency."
  - *Blocking mode* means that if the guardrail trips, "the agent never executes, preventing token consumption and tool execution."
  - Motivating example: run a guardrail "with a fast/cheap model" before an expensive one.

  [docs] — [OpenAI Agents SDK, Guardrails (accessed)](https://openai.github.io/openai-agents-python/guardrails/)
- **OpenAI's customer-service reference demo (airline).**
  - A Triage Agent routes to specialist agents: Flight Information, Booking & Cancellation, Seat & Special Services, FAQ, and Refunds & Compensation.
  - Relevance and Jailbreak guardrails both reply "Sorry, I can only answer questions related to airline travel."

  [docs] — [openai/openai-cs-agents-demo (GitHub)](https://github.com/openai/openai-cs-agents-demo)

#### LangChain / LangGraph
- **Harrison Chase (April 20, 2025).**
  - The hard part of building reliable agents is "making sure the LLM has the appropriate context at each step."
  - Agent abstractions can "obscure and complicate efforts to ensure the LLM receives the appropriate context at each step."
  - Most production systems combine workflows and agents.
  - LangGraph is positioned as an orchestration framework with both declarative and imperative APIs, with agent abstractions built on top.

  [eng] — [LangChain blog, "How to think about agent frameworks", 2025-04-20](https://www.langchain.com/blog/how-to-think-about-agent-frameworks)
- **Klarna.** Its assistant was built on LangGraph and LangSmith as "a controllable agent architecture." [vendor case study, search extract] — [LangChain, Klarna case study](https://www.langchain.com/blog/customers-klarna)

#### Pydantic AI
- **Testing without real models.**
  - "The simplest and fastest way to exercise most of your application code is using `TestModel`."
  - `FunctionModel` gives you scripted control over tool calls.
  - `Agent.override` swaps the model without touching call sites.
  - "Set `ALLOW_MODEL_REQUESTS=False` globally to block any requests from being made to non-test models accidentally."
  - `capture_run_messages` lets tests assert on the exact message exchange.

  [docs] — [Pydantic AI docs, Testing (accessed)](https://pydantic.dev/docs/ai/guides/testing/)
- **Validation and retries** work as described in section 3. [docs] — [Pydantic AI docs, Retries](https://pydantic.dev/docs/ai/core-concepts/retries/index.md)

#### Microsoft, Google and Salesforce
- **Microsoft Agent Framework workflows.** Capabilities include:
  - human-in-the-loop ("Pause for external input and resume execution");
  - checkpoints and resuming;
  - observability ("Export workflow spans, metrics, events, and delivery status");
  - declarative workflows;
  - orchestrations: sequential, concurrent, handoff, group-chat and Magentic.

  [docs] — [Microsoft Learn, Agent Framework workflow capabilities (ms.date 2026-07-29)](https://learn.microsoft.com/en-us/agent-framework/workflows/)
- **Google ADK.** Workflow agents orchestrate deterministically, without an LLM. ADK 2.0 supersedes them with graph-based and dynamic workflows. [docs] — [Google ADK docs, Workflow agents](https://adk.dev/agents/workflow-agents/)
- **Salesforce architect guidance** (vendor docs, but it generalises): "Architects should push deterministic logic by default, reserving the LLM for cases where the task actually requires its reasoning capability." [docs] — [Salesforce Architects, Hybrid Reasoning](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)

### Inferences
- **The guidance supports "a workflow skeleton with agent islands."**
  - The four claims phases form a workflow: prompt chaining with gates.
  - Each turn uses routing (intent or command classification).
  - Only the grounded Q&A inside case processing behaves like an agent.
- **A two-artifact authoring model.** Combining OpenAI's advice (turn existing SOPs into routines) with Salesforce's (push to code by default) suggests turning the SOP text into two things:
  - state-machine or flow definitions for gates and step order;
  - per-step natural-language instructions for wording and judgement.

### Gaps
- **Not fetched this session.** LangGraph's docs on interrupts, checkpointers and time-travel, and the Microsoft Agent Framework concept pages on executors and type safety. Only the capabilities overview was read.
- **Missing Google guidance.** I found no Google best-practice guidance on mixing playbooks and flows, for example where authentication should sit.

## 6. Production engineering lessons: latency, cost, observability and tracing, fallbacks for failed or invalid LLM output, prompt and model versioning, deterministic replayable testing

### Takeaway
The hybrid architectures are justified as much by cost and latency as by control. The levers vendors cite:
- remove LLM calls from transitions (Agentforce; Rasa's `minimize_num_calls`);
- use small fine-tuned models for parsing (Rasa's Llama-3.1-8B command generator; Sierra's low-latency models);
- run checks in parallel (Parlant; OpenAI guardrails);
- cache static prompt prefixes (Anthropic: −53% cost and −75% latency for a 10-turn conversation);
- hide latency with quick acknowledgements.

Operationally, vendors converge on:
- tracing every decision at span level;
- failing over between model providers;
- escalating to a human when validation fails;
- version control with pinned models;
- simulation and end-to-end tests that stub out actions or mock the model.

### Cited Findings

#### Latency and cost
- **Agentforce v1 cost.** At least three LLM cycles per simple turn and five or more for multi-step tasks. "Every unnecessary LLM call adds latency, cost, and variability." [docs] — [Salesforce Architects, Hybrid Reasoning](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- **Rasa vs LangGraph numbers.** $0.04 vs $0.10 per message; 2.08 s vs 7.4 s; 1.58 s with an NLU front end. [bench] — [Rasa blog, 2024-08-08](https://rasa.com/blog/cutting-ai-assistant-costs-the-power-of-enhancing-llms-with-business)
- **Rasa `minimize_num_calls`.** Skips the LLM when classic NLU has already resolved the turn. [docs] — [Rasa docs, LLM Command Generators](https://rasa.com/docs/reference/config/components/llm-command-generators/)
- **Rasa small-model option.** Rasa publishes a command-generation dataset whose pairs "can be used to train a small LLM like Llama 3.1 8b to act as a command generator." It also publishes a fine-tuned model, `rasa/command-generator-llama-3.1-8b-instruct`, evaluated by "F1 score per command type (StartFlow, SetSlot, etc.)." [docs] — [Hugging Face, rasa/command-generation-calm-demo-v1](https://huggingface.co/datasets/rasa/command-generation-calm-demo-v1); [Hugging Face, rasa/command-generator-llama-3.1-8b-instruct](https://huggingface.co/rasa/command-generator-llama-3.1-8b-instruct)
- **Sierra model choice for speed.** Sierra picks models that "satisfy tighter latency constraints for natural-sounding voice conversations." It notes that "models that shine at reasoning often degrade significantly when forced to produce a quicker response." [eng] — [Sierra, Constellation of models, 2025-12-03](https://sierra.ai/blog/constellation-of-models)
- **Parlant 3.0 (vendor claims).**
  - "Journey state matching happens in parallel with guideline evaluation, reducing response latency by up to 60%."
  - The engine predicts which journeys will activate so it can prepare ahead.
  - "Instant acknowledgments" improve perceived speed.
  - "Only relevant journeys are loaded into the LLM context."

  [mkt/eng] — [Parlant 3.0 blog, 2025-08-15](https://parlant.io/blog/parlant-3-0-release/)
- **Google routine playbooks.** They have "improved latency" because transitions happen within a single turn. [docs] — [Google Cloud docs, Playbooks](https://docs.cloud.google.com/dialogflow/cx/docs/concept/playbook)
- **OpenAI Agents SDK guardrails.** Parallel guardrails give the best latency; blocking guardrails avoid wasting tokens. [docs] — [OpenAI Agents SDK, Guardrails](https://openai.github.io/openai-agents-python/guardrails/)
- **Anthropic prompt caching (announced August 14, 2024).** Caching cuts "costs by up to 90% and latency by up to 85% for long prompts."

  | Use case | Latency | Cost |
  |---|---|---|
  | Chat with a book (100k tokens) | −79% | −90% |
  | Many-shot prompting (10k tokens) | −31% | −86% |
  | Multi-turn conversation (10 turns) | −75% | −53% |

  Writing to the cache costs 25% more than the base input price; reading from it costs 10% of the base price. My fetch of the redirected claude.com page displayed a 2025 date, but the models it lists (Claude 3.5 Sonnet, 3 Opus, 3 Haiku) point to the 2024 original. [eng] — [Anthropic / Claude blog, "Prompt caching"](https://claude.com/blog/prompt-caching)
- **OpenAI Structured Outputs latency.** The first request with a new schema takes under 10 seconds typically, and up to a minute for complex schemas. [eng] — [OpenAI, Structured Outputs, 2024-08-06](https://openai.com/index/introducing-structured-outputs-in-the-api/)

#### Reliability, fallbacks and error handling
- **Sierra.**
  - Provider redundancy: "When a provider starts to degrade, our automated routing seamlessly fails over to healthier, equivalent models."
  - It monitors "latency, error rates, and timeouts."

  [eng] — [Sierra, Constellation of models](https://sierra.ai/blog/constellation-of-models)
- **Fin.** If any safety or accuracy check fails, Fin tells the customer it can't answer and "escalate[s] to human support." Below its certainty threshold it asks a clarifying question instead. [docs] — [Intercom Help, Fin AI Engine](https://www.intercom.com/help/en/articles/9929230-the-fin-ai-engine)
- **Pydantic AI.** Validation failures become retry prompts, and `UnexpectedModelBehavior` is raised once the retry budget (default 1) is used up. [docs] — [Pydantic AI docs, Retries](https://pydantic.dev/docs/ai/core-concepts/retries/index.md)
- **OpenAI Structured Outputs failure cases.** A `refusal`, truncation at `max_tokens`, or wrong values inside otherwise valid JSON. [eng] — [OpenAI, Structured Outputs](https://openai.com/index/introducing-structured-outputs-in-the-api/)
- **Rasa.** Built-in fallback patterns: `pattern_internal_error`, `pattern_cannot_handle`, and `pattern_human_handoff`. [docs] — [Rasa docs, Patterns](https://rasa.com/docs/reference/primitives/patterns/)
- **OpenAI guide.** Escalate to a human after exceeding retry or failure thresholds, and add human oversight for high-risk actions. [docs] — [OpenAI practical guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- **Agentforce.** Action loops happen when a gate stays open, so gates must be closed deterministically after the action runs. [docs] — [Salesforce Architects, Hybrid Reasoning](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)

#### Observability and audit
- **OpenAI Agents SDK tracing.**
  - Traced by default: agent spans; "LLM generations are wrapped in `generation_span()`"; function tool calls; "Guardrails are wrapped in `guardrail_span()`"; and "Handoffs are wrapped in `handoff_span()`."
  - Setting `RunConfig.trace_include_sensitive_data=False` keeps LLM inputs and outputs and tool parameters out of traces.

  [docs] — [OpenAI Agents SDK, Tracing (accessed)](https://openai.github.io/openai-agents-python/tracing/)
- **Parlant.** OpenTelemetry tracing logs every guideline match and decision. [docs] — [Parlant README](https://github.com/emcie-co/parlant). Since 3.2, labels on guidelines and journeys propagate to sessions. [docs] — [Parlant 3.2 blog](https://parlant.io/blog/parlant-3-2-streaming-responses/)
- **Agentforce.** Results on the deterministic path pass "back through the trust layer with full audit logging." Its argument for this design is that the execution path must be reproducible for audit. [docs] — [Salesforce Architects, Hybrid Reasoning](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- **Copilot Studio.** An activity map shows how the orchestrator responded during testing. [docs] — [Microsoft Learn, generative orchestration](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-generative-actions)
- **Decagon.** It offers "explanations of your agent's reasoning at any point in the conversation, with runtime and latency numbers," and lets you "trace agent reasoning, audit behavior." [mkt] — [Decagon AOP product page](https://decagon.ai/product/aop)
- **Sierra.** It combines "fast, high recall detection with slower, high precision reasoning so the system automatically surfaces and logs defects." [eng] — [Sierra, enterprise-grade agents, 2025-10-02](https://sierra.ai/blog/enterprise-grade-agents)

#### Versioning and change control
- **Decagon.** Teams "version with Git-based tracking." [mkt] — [Decagon AOP product page](https://decagon.ai/product/aop)
- **Copilot Studio.** "Some behaviors... depend on the model your agent is configured to use. Test with the model your agent uses, because changing the model can change the result." [docs] — [Microsoft Learn, generative orchestration](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-generative-actions)
- **Sierra.** "Prompting differs across model families." The modular design lets you "update high-value, low-risk tasks without forcing changes to sensitive guardrails." [eng] — [Sierra, Constellation of models](https://sierra.ai/blog/constellation-of-models)
- **Agentforce.** The authored artifact (Agent Script) is kept separate from the compiled execution artifact (Agent Graph), and the platform enforces behaviour "independently of how the script was written." [docs] — [Salesforce Architects, Hybrid Reasoning](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)

#### Deterministic and simulated testing
- **Pydantic AI.** `TestModel`, `FunctionModel`, `Agent.override`, `ALLOW_MODEL_REQUESTS=False`, and `capture_run_messages`. Real models are avoided in tests because of cost, latency and variability. [docs] — [Pydantic AI docs, Testing](https://pydantic.dev/docs/ai/guides/testing/)
- **Rasa end-to-end tests.**
  - YAML test cases with user and bot steps.
  - Assertions: `flow_started`, `flow_completed`, `flow_cancelled`, `slot_was_set`, `slot_was_not_set`, `bot_uttered`, `action_executed`, `generative_response_is_relevant`, `generative_response_is_grounded`.
  - Custom actions can be stubbed "so they don't run external API calls."
  - "Integrate E2E tests into your CI/CD pipeline to catch regressions."

  [docs] — [Rasa docs, Evaluating your assistant / E2E testing (accessed)](https://rasa.com/docs/pro/testing/evaluating-assistant/)
- **Intercom Simulations.** An AI plays the customer and judges each conversation against success criteria you set. [docs] — [Intercom Help, Fin Procedures explained](https://www.intercom.com/help/en/articles/12495167-fin-procedures-explained)
- **τ-bench.** LLM-simulated users, grading by comparing the final database state with the goal, and pass^k to measure consistency over k tries. [paper] — [τ-bench, arXiv 2406.12045](https://arxiv.org/abs/2406.12045)
- **Agentforce.** "Action simulation emulates responses in the Preview and Simulate environment instead of executing live." [docs] — [Salesforce Architects, Hybrid Reasoning](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- **Anthropic.** "Extensive testing in sandboxed environments." [docs] — [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)

### Inferences
- **A latency and cost budget for the claims agent.**
  - One small-model extraction or command call per turn: structured output, temperature 0, with the static prefix (schema and SOP) cached.
  - A deterministic transition in code.
  - Then either a canned or template reply (no LLM call) or one wording call.
  - A groundedness check only on free Q&A turns.
  - An asynchronous supervisor or audit pass kept off the critical path.

  This approximates Rasa's roughly 2 seconds per turn rather than the three-to-five-call ReAct profile.
- **Replayable tests.**
  - Log per turn: user text, the extractor's input and output JSON, the state change, the phase, tool calls, prompt version, and model id.
  - Replay by feeding the logged extractor JSON through a `FunctionModel`-style stub, so controller logic is tested deterministically.
  - Separately, run LLM-in-the-loop simulations scored with pass^k.
- **Redact health data by default.** For healthcare claims, traces should hide sensitive data unless explicitly enabled, following the Agents SDK `trace_include_sensitive_data=False` pattern and the OpenAI guide's PII filter.

### Gaps
- **No per-conversation economics.** Sierra, Decagon, Fin and Ada publish no per-conversation cost or end-to-end latency figures. Fin's "~0.01% hallucination rate" appears only in marketing search extracts and is unverified.
- **No production failure rates.** There is no public data on JSON or schema failure rates after constrained decoding, or on latency added by retries.
- **Prompt-versioning practice is undocumented.** The vendors fetched here do not describe rollout, A/B testing or rollback of prompts in detail. LangSmith and Langfuse prompt-management docs were not fetched.
- **Rasa e2e determinism unclear.** Rasa's docs don't say whether LLM calls are mocked or cached in e2e tests, so run-to-run determinism with a live LLM is not addressed.
