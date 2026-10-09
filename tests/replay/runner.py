from datetime import date
from pathlib import Path

import yaml

from app.config import Settings
from app.data.repos import build_repos
from app.data.store import FixtureStore
from app.engine.briefs import render_brief
from app.engine.guard import OutputGuard
from app.engine.machine import Engine
from app.engine.service import ConversationService
from app.llm.fake import FakeLLM
from app.llm.schemas import TurnAnalysis
from app.observability.trace import TraceWriter

FIXTURES = Path(__file__).parent / "fixtures"
EXPECT_KEYS = {"phase", "verified", "party_id", "attempts", "pending_ask", "escalated", "off_topic",
               "outbox_len", "reply_contains", "reply_not_contains", "guard_ok"}


class ScriptedFakeLLM(FakeLLM):
    """The offline Writer renders the brief, unless the fixture scripts what the Writer says on a turn
    (`writer:` one text per attempt), so the guard path has an offline test that can fail."""

    def __init__(self, analyses: list[TurnAnalysis], scripts: list[list[str]]):
        super().__init__(analyses)
        self.scripts, self.turn = scripts, -1

    def analyze(self, **kw):
        self.turn += 1
        return super().analyze(**kw)

    def compose(self, *, brief, transcript, violation=None):
        texts = self.scripts[self.turn] if 0 <= self.turn < len(self.scripts) else []
        return texts.pop(0) if texts else render_brief(brief)


def scenario_names() -> list[str]:
    return sorted(p.stem for p in FIXTURES.glob("*.yaml"))


def load(name: str) -> dict:
    with open(FIXTURES / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_scenario(spec: dict, settings: Settings) -> list[dict]:
    store = FixtureStore.load(settings.fixtures_dir)
    repos = build_repos(store, settings)
    today = date.fromisoformat(spec.get("today", "2026-10-07"))
    llm = ScriptedFakeLLM([TurnAnalysis.model_validate(t.get("analysis", {})) for t in spec["turns"]],
                          [list(t.get("writer", [])) for t in spec["turns"]])
    svc = ConversationService(Engine(repos, settings, lambda: today), llm, OutputGuard(store), repos,
                              settings, TraceWriter(settings.traces_dir))
    session = svc.start(spec.get("scenario", "default"))
    results = []
    for t in spec["turns"]:
        verified_before = session.verification.status == "verified"
        res = svc.chat(session, t["user"])
        results.append({
            "reply": res.reply, "session": session.model_copy(deep=True), "guard": session.last_guard,
            "outbox_len": len(svc.outbox(session)), "verified_before": verified_before,
            "verified_after": session.verification.status == "verified",
        })
    return results


def assert_turn(i: int, turn_spec: dict, result: dict) -> None:
    e = turn_spec.get("expect", {})
    s, reply = result["session"], result["reply"]
    low = reply.lower()
    ctx = f"turn {i + 1} ({turn_spec['user'][:40]!r})"
    assert set(e) <= EXPECT_KEYS, f"{ctx}: unknown expect keys {sorted(set(e) - EXPECT_KEYS)}"
    if "phase" in e:
        assert s.phase.value == e["phase"], f"{ctx}: phase {s.phase.value} != {e['phase']}"
    if "verified" in e:
        assert (s.verification.status == "verified") == e["verified"], (
            f"{ctx}: verified {s.verification.status}")
    if "party_id" in e:
        assert s.verification.party_id == e["party_id"], ctx
    if "attempts" in e:
        assert s.verification.attempts == e["attempts"], f"{ctx}: attempts {s.verification.attempts}"
    if "pending_ask" in e:
        assert s.pending_ask.value == e["pending_ask"], f"{ctx}: pending {s.pending_ask.value}"
    if "escalated" in e:
        assert s.escalation.requested == e["escalated"], ctx
    if "off_topic" in e:
        assert s.counters.off_topic == e["off_topic"], f"{ctx}: off_topic {s.counters.off_topic}"
    if "outbox_len" in e:
        assert result["outbox_len"] == e["outbox_len"], ctx
    for sub in e.get("reply_contains", []):
        assert sub.lower() in low, f"{ctx}: missing {sub!r} in {reply!r}"
    for sub in e.get("reply_not_contains", []):
        assert sub.lower() not in low, f"{ctx}: leaked {sub!r} in {reply!r}"
    if "guard_ok" in e:  # true: the guard passed with no fallback; false: the guard caught something
        g = result["guard"]
        caught = not g["ok"] or bool(g.get("draft_violations"))
        assert (g["ok"] and not g.get("fallback")) if e["guard_ok"] else caught, f"{ctx}: guard {g}"
