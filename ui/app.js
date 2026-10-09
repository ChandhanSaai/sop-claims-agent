const PHASES = ["VERIFY_ID", "RESOLVE_INTENT", "PROCESS_CASE", "POST_PROCESS"];
const TOKEN_KEY = "sop-claims-agent.token";
const EXAMPLES = [
  "Hi, I'm Margaret Chen, policy POL-9921. My healthcare claim from January was denied and I want to know why.",
  "I'm David Chen, calling for my mother Margaret Chen, policy POL-9921, about her denied healthcare claim.",
  "What is reinforcement learning?",
];
const NEEDS_TOKEN = "This server needs an access token. Use the Access token button above to enter it.";
const $ = (id) => document.getElementById(id);
let sessionId = null;
let token = "";
let linkToken = "";  // a token that arrived in the link; used only after the person confirms it in the dialog
let busy = false;

// Every piece of model or server text is rendered through el() and textContent, never as HTML.
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Tokens are printable ASCII; anything else (a zero-width space from a copy, a curly quote) is dropped so the
// header value stays valid and a bad paste becomes a plain 401 rather than a failed request.
function cleanToken(value) {
  return (value || "").replace(/[^\x20-\x7E]/g, "").trim();
}

function loadToken() {
  try { token = cleanToken(localStorage.getItem(TOKEN_KEY)); } catch (err) { token = ""; }
  const m = location.hash.match(/[#&]token=([^&]*)/);
  if (m) {
    try { linkToken = cleanToken(decodeURIComponent(m[1])); } catch (err) { linkToken = ""; }
    history.replaceState(null, "", location.pathname + location.search);  // the fragment leaves the address bar
  }
}

function saveToken(value) {
  token = cleanToken(value);
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token); else localStorage.removeItem(TOKEN_KEY);
  } catch (err) { /* storage unavailable: the token still works for this page */ }
}

function headers() {
  const h = { "Content-Type": "application/json" };
  if (token) h["X-Access-Token"] = token;
  return h;
}

function showTokenState(required) {
  const button = $("token-button");
  button.hidden = !required;
  button.textContent = token ? "Access token: set" : "Access token";
}

function showTokenDialog({ rejected = false, prefill = "" } = {}) {
  $("token-error").hidden = !rejected;
  if (rejected) $("token-input").setAttribute("aria-describedby", "token-error");
  else $("token-input").removeAttribute("aria-describedby");
  $("token-input").value = prefill || (rejected ? "" : token);
  const dialog = $("token-dialog");
  if (!dialog.open) dialog.showModal();
  $("token-input").focus();
  $("token-input").select();
}

function timestamp() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function addMessage(role, text) {
  const wrap = el("div", `msg ${role}`);
  if (role !== "system") {
    wrap.appendChild(el("div", "meta", `${role === "user" ? "You" : "Assistant"} · ${timestamp()}`));
  }
  wrap.appendChild(el("div", "bubble", text));
  $("messages").appendChild(wrap);
  $("messages").scrollTop = $("messages").scrollHeight;
  return wrap;
}

function showExamples() {
  const box = el("div", "examples");
  box.appendChild(el("p", "examples-title", "Try one of these, or write your own message:"));
  EXAMPLES.forEach((text) => {
    const chip = el("button", "chip", text);
    chip.type = "button";
    chip.addEventListener("click", () => { if (!busy) { $("input").value = text; sendMessage(); } });
    box.appendChild(chip);
  });
  $("messages").appendChild(box);
}

function pill(text, kind) {
  return el("span", `pill${kind ? ` ${kind}` : ""}`, text);
}

function renderPhases(current) {
  const idx = PHASES.indexOf(current);
  $("phases").replaceChildren(...PHASES.map((p, i) => {
    const li = el("li", i < idx ? "done" : i === idx ? "active" : "", p.replace("_", " "));
    li.title = p;
    return li;
  }));
}

function renderStatus(state) {
  const v = state.verification;
  const verification = pill(v.status, v.status === "verified" ? "ok" : v.status === "exhausted" ? "bad" : "");
  const rows = [
    ["Turn", String(state.turn)],
    ["Verification", verification],
    ["Role / attempts", `${v.role ?? "-"} / ${v.attempts}`],
    ["Pending ask", state.pending_ask],
    ["Selected claim", state.case.selected_case_id ?? "-"],
    ["Intent", state.case.intent ?? "-"],
    ["Off-topic turns", String(state.counters.off_topic)],
    ["Frustration streak", String(state.counters.frustration_streak)],
    ["Gate explanations", String(state.counters.gate_explanations)],
    ["Abusive turns", String(state.counters.abusive)],
    ["Email offered", state.counters.email_offered ? "yes" : "no"],
    ["Human declined", state.counters.human_declined ? "yes" : "no"],
    ["Escalation", state.escalation.requested ? pill(state.escalation.reference ?? "requested", "warn") : "no"],
    ["Fence", state.fence_turn ? `turn ${state.fence_turn}` : "none"],
    ["Consent", state.consent.status === "none" ? "none" : pill(state.consent.status,
      state.consent.status === "approved" ? "ok" : state.consent.status === "timed_out" ? "bad" : "warn")],
    ["Closed", state.closed ? pill("yes", "bad") : "no"],
  ];
  $("status").replaceChildren(...rows.flatMap(([k, v]) => {
    const dd = el("dd");
    if (typeof v === "string") dd.textContent = v; else dd.appendChild(v);
    return [el("dt", "", k), dd];
  }));
}

function renderMemory(memory) {
  const entries = Object.entries(memory);
  if (!entries.length) {
    const tr = el("tr");
    tr.appendChild(el("td", "empty", "Nothing remembered yet."));
    $("memory").replaceChildren(tr);
    return;
  }
  $("memory").replaceChildren(...entries.map(([name, slot]) => {
    const tr = el("tr");
    tr.appendChild(el("td", "", name));
    tr.appendChild(el("td", "", slot.value));
    const status = el("td");
    status.appendChild(pill(slot.status, slot.status === "verified" ? "ok" : slot.status === "rejected" ? "bad" : ""));
    tr.appendChild(status);
    tr.appendChild(el("td", "", `turn ${slot.source_turn}`));
    return tr;
  }));
}

function renderBrief(brief) {
  const box = $("brief");
  if (!brief) { box.replaceChildren(el("p", "empty", "No brief yet.")); return; }
  const dl = el("dl");
  const row = (label, node) => {
    const r = el("div", "row");
    r.appendChild(el("dt", "", label));
    const dd = el("dd");
    if (typeof node === "string") dd.textContent = node; else dd.appendChild(node);
    r.appendChild(dd);
    dl.appendChild(r);
  };
  const list = (items) => {
    const ul = el("ul");
    items.forEach((t) => ul.appendChild(el("li", "", t)));
    return ul;
  };
  row("Phase", brief.phase);
  row("Goal", brief.goal);
  row("Tone", brief.tone + (brief.acknowledge ? ` (acknowledge: ${brief.acknowledge})` : ""));
  const facts = Object.entries(brief.allowed_facts || {});
  if (facts.length) {
    const table = el("table");
    facts.forEach(([k, v]) => {
      const tr = el("tr");
      tr.appendChild(el("td", "", k));
      tr.appendChild(el("td", "", String(v)));
      table.appendChild(tr);
    });
    row("Allowed facts", table);
  } else {
    row("Allowed facts", "none (nothing about any claim may be stated)");
  }
  if (brief.must_say?.length) row("Must say", list(brief.must_say));
  if (brief.must_not?.length) row("Must not", list(brief.must_not));
  if (brief.options?.length) row("Options", list(brief.options));
  if (brief.ask) row("Ask", brief.ask);
  if (brief.offer_human) row("Human offer", "yes");
  if (brief.verbatim) row("Verbatim", brief.verbatim);
  box.replaceChildren(dl);
}

function renderGuard(guard) {
  const box = $("guard");
  if (!guard) { box.replaceChildren(el("p", "empty", "No reply checked yet.")); return; }
  const frag = document.createDocumentFragment();
  if (guard.ok && !guard.fallback) {
    frag.appendChild(pill(guard.regenerated ? "passed after one regeneration" : "passed", "ok"));
  } else if (guard.fallback) {
    frag.appendChild(pill(`fallback: ${guard.fallback}`, "bad"));
  } else {
    frag.appendChild(pill("violations", "bad"));
  }
  if (guard.violations?.length) {
    const ul = el("ul", "violations");
    guard.violations.forEach((v) => ul.appendChild(el("li", "", v)));
    frag.appendChild(ul);
  }
  box.replaceChildren(frag);
}

function renderEvents(events, fence) {
  const recent = events.slice(-12);
  if (!recent.length) { $("events").replaceChildren(el("li", "empty", "No events yet.")); return; }
  $("events").replaceChildren(...recent.map((e) => {
    const li = el("li", e.turn < fence ? "fenced" : "");
    if (e.turn < fence) li.title = "Before the verification reset: not reused for the party verified since.";
    li.appendChild(el("span", "turn", `t${e.turn}`));
    li.appendChild(document.createTextNode(e.type));
    return li;
  }));
}

function renderState(state) {
  renderPhases(state.phase);
  renderStatus(state);
  renderMemory(state.memory);
  renderBrief(state.last_brief);
  renderGuard(state.last_guard);
  renderEvents(state.events, state.fence_turn);
}

async function refreshOutbox() {
  try {
    const r = await fetch(`/api/session/${sessionId}/outbox`, { headers: headers() });
    if (!r.ok) { addMessage("system", `Could not load the outbox (${r.status}).`); return; }
    const { emails } = await r.json();
    if (!emails.length) { $("outbox").replaceChildren(el("li", "empty", "Nothing sent.")); return; }
    $("outbox").replaceChildren(...emails.map((e) => el("li", "", `${e.id} to ${e.to_masked}: ${e.subject}`)));
  } catch (err) {
    addMessage("system", `Could not load the outbox (${err.message}).`);
  }
}

function clearInspector() {
  ["phases", "status", "memory", "brief", "guard", "outbox", "events"].forEach((id) => $(id).replaceChildren());
  $("trace").textContent = "";
}

function setBusy(on) {
  busy = on;
  ["send", "input", "new", "token-button"].forEach((id) => { $(id).disabled = on; });
}

async function newConversation() {
  if (busy) return;
  if (linkToken) {  // a token from the link is never used silently: the person confirms it first
    const prefill = linkToken;
    linkToken = "";
    showTokenState(true);
    showTokenDialog({ prefill });
    return;
  }
  sessionId = null; // a failed restart must not keep chatting into the old, now cleared, conversation
  $("messages").replaceChildren();
  clearInspector();
  let data;
  try {
    const r = await fetch("/api/session", {
      method: "POST", headers: headers(), body: JSON.stringify({ scenario: $("scenario").value }),
    });
    if (r.status === 401) {
      const rejected = Boolean(token);
      if (rejected) saveToken("");
      showTokenState(true);
      addMessage("system", NEEDS_TOKEN);
      showTokenDialog({ rejected });
      return;
    }
    if (!r.ok) { addMessage("system", `Could not start a conversation (${r.status}).`); return; }
    data = await r.json();
  } catch (err) {
    addMessage("system", `Network error: ${err.message}`); return;
  }
  showTokenState(Boolean(token));
  sessionId = data.session_id;
  addMessage("assistant", data.greeting);
  showExamples();
  renderState(data.state);
  await refreshOutbox();
  $("input").focus();
}

async function sendMessage() {
  const text = $("input").value.trim();
  if (!text || !sessionId || busy) return;
  $("input").value = "";
  $("input").style.height = "";
  document.querySelector(".examples")?.remove();
  addMessage("user", text);
  const typing = addMessage("assistant typing", "");
  setBusy(true);
  let data;
  try {
    const r = await fetch("/api/chat", {
      method: "POST", headers: headers(), body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    if (r.status === 401) { saveToken(""); showTokenState(true); showTokenDialog({ rejected: true }); return; }
    if (!r.ok) { addMessage("system", `The request failed (${r.status}).`); return; }
    data = await r.json();
  } catch (err) {
    addMessage("system", `Network error: ${err.message}`); return;
  } finally {
    typing.remove();
    setBusy(false);
    $("input").focus();
  }
  addMessage("assistant", data.reply);
  renderState(data.state);
  $("trace").textContent = JSON.stringify(data.trace, null, 1);
  await refreshOutbox();
}

$("form").addEventListener("submit", (ev) => { ev.preventDefault(); sendMessage(); });
$("input").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); sendMessage(); }
});
$("input").addEventListener("input", (ev) => {
  const box = ev.target;
  box.style.height = "";
  box.style.height = `${Math.min(box.scrollHeight + box.offsetHeight - box.clientHeight, 160)}px`;
});
$("token-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const entered = cleanToken($("token-input").value);
  const changed = entered !== token;
  saveToken(entered);
  $("token-dialog").close();
  showTokenState(true);
  if (!sessionId || changed) newConversation();  // an unchanged token mid-conversation just closes the dialog
});
$("token-dialog").addEventListener("cancel", (ev) => {
  if (!sessionId || !token) {  // nothing works without a token; keep the dialog up and explain if Escape gets through
    ev.preventDefault();
    if (!$("messages").textContent.includes(NEEDS_TOKEN)) addMessage("system", NEEDS_TOKEN);
  }
});
$("token-button").addEventListener("click", () => showTokenDialog());
$("new").addEventListener("click", newConversation);
loadToken();
newConversation();
