"""Render the golden transcripts (FakeLLM replay) as markdown tables for the README."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from tests.replay.runner import load, run_scenario  # noqa: E402


def render(name: str) -> str:
    spec = load(name)
    settings = Settings(_env_file=None, fixtures_dir=Path("fixtures"), traces_dir=Path("traces") / "render",
                        llm_backend="fake")
    rows = ["| # | Caller says | Phase after | Verified | Pending ask | Guard |", "|---|---|---|---|---|---|"]
    for i, (t, r) in enumerate(zip(spec["turns"], run_scenario(spec, settings), strict=True), start=1):
        s = r["session"]
        guard = "ok" if r["guard"]["ok"] else "violation"
        rows.append(f"| {i} | {t['user']} | {s.phase.value} | {s.verification.status} | {s.pending_ask.value}"
                    f" | {guard} |")
    return f"### {spec['name']}\n\n" + "\n".join(rows) + "\n"


if __name__ == "__main__":
    for name in sys.argv[1:] or ["margaret_happy_path", "angry_caller"]:
        print(render(name))
