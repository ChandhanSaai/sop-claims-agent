const PHASES = ["VERIFY_ID", "RESOLVE_INTENT", "PROCESS_CASE", "POST_PROCESS"];
const $ = (id) => document.getElementById(id);
let sessionId = null;

function headers() {
  const h = { "Content-Type": "application/json" };
  const t = $("token").value.trim();
  if (t) h["X-Access-Token"] = t;
  return h;
}

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text; // never innerHTML: model text is untrusted
  $("messages").appendChild(div);
  $("messages").scrollTop = $("messages").scrollHeight;
}

function setText(el, text) { el.textContent = text; }

function renderState(state) {
  const stepper = $("phases");
  stepper.replaceChildren(...PHASES.map((p) => {
    const span = document.createElement("span");
    span.textContent = p;
    span.className = p === state.phase ? "active" : "";
    return span;
  }));
  const rows = [
    ["Turn", state.turn], ["Verification", `${state.verification.status} (${state.verification.role ?? "-"}, attempts ${state.verification.attempts})`],
    ["Pending ask", state.pending_ask], ["Selected claim", state.case.selected_case_id ?? "-"], ["Intent", state.case.intent ?? "-"],
    ...Object.entries(state.counters),
    ["Escalation", state.escalation.requested ? (state.escalation.reference ?? "-") : "no"], ["Consent", state.consent.status],
    ["Closed", state.closed ? "yes" : "no"],
  ];
  $("status").replaceChildren(...rows.flatMap(([k, v]) => {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = String(v);
    return [dt, dd];
  }));
  $("memory").replaceChildren(...Object.entries(state.memory).map(([name, slot]) => {
    const tr = document.createElement("tr");
    [name, slot.value, slot.status, `turn ${slot.source_turn}`].forEach((c) => {
      const td = document.createElement("td"); td.textContent = c; tr.appendChild(td);
    });
    return tr;
  }));
  setText($("brief"), state.last_brief ? JSON.stringify(state.last_brief, null, 1) : "");
  setText($("guard"), state.last_guard ? JSON.stringify(state.last_guard, null, 1) : "");
  $("events").replaceChildren(...state.events.slice(-8).map((e) => {
    const li = document.createElement("li"); li.textContent = `t${e.turn} ${e.type}`; return li;
  }));
}

async function refreshOutbox() {
  try {
    const r = await fetch(`/api/session/${sessionId}/outbox`, { headers: headers() });
    if (!r.ok) return;
    const { emails } = await r.json();
    $("outbox").replaceChildren(...emails.map((e) => {
      const li = document.createElement("li"); li.textContent = `${e.id} to ${e.to_masked}: ${e.subject}`; return li;
    }));
  } catch (err) {
    addMessage("system", `Could not load the outbox (${err.message}).`);
  }
}

async function newConversation() {
  sessionId = null; // a failed restart must not keep chatting into the old, now cleared, conversation
  $("messages").replaceChildren();
  ["phases", "status", "memory", "brief", "guard", "outbox", "events", "trace"].forEach((id) => $(id).replaceChildren());
  let data;
  try {
    const r = await fetch("/api/session", { method: "POST", headers: headers(), body: JSON.stringify({ scenario: $("scenario").value }) });
    if (r.status === 401) { addMessage("system", "Enter the access token above and click New conversation."); return; }
    if (!r.ok) { addMessage("system", `Could not start a session (${r.status}).`); return; }
    data = await r.json();
  } catch (err) {
    addMessage("system", `Network error: ${err.message}`); return;
  }
  sessionId = data.session_id;
  addMessage("assistant", data.greeting);
  renderState(data.state);
  await refreshOutbox();
}

$("form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = $("input").value.trim();
  if (!text || !sessionId) return;
  $("input").value = "";
  addMessage("user", text);
  const typing = document.createElement("div"); typing.className = "msg assistant typing"; typing.textContent = "…";
  $("messages").appendChild(typing);
  let data;
  try {
    const r = await fetch("/api/chat", { method: "POST", headers: headers(), body: JSON.stringify({ session_id: sessionId, message: text }) });
    if (!r.ok) { addMessage("system", `Request failed (${r.status}).`); return; }
    data = await r.json();
  } catch (err) {
    addMessage("system", `Network error: ${err.message}`); return;
  } finally {
    typing.remove();
  }
  addMessage("assistant", data.reply);
  renderState(data.state);
  setText($("trace"), JSON.stringify(data.trace, null, 1));
  await refreshOutbox();
});

$("new").addEventListener("click", newConversation);
newConversation();
