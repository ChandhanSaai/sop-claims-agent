import sys

import pytest

from scripts import live_replay


def run(name: str, hard: tuple[str, ...] = ()) -> dict:
    return {"name": name, "turns": [{"i": 1, "hard": list(hard), "soft": []}]}


def test_pass_n_is_one_only_when_all_runs_passed(settings, tmp_path, monkeypatch):
    out = tmp_path / "live-reliability.md"
    monkeypatch.setattr(live_replay, "RELIABILITY", out)
    miss = "phase: expected 'PROCESS_CASE', got 'VERIFY_ID'"
    runs = [[run("flaky"), run("steady")], [run("flaky"), run("steady")],
            [run("flaky", (miss,)), run("steady")]]
    live_replay.write_reliability(runs, settings)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert f"| flaky | 1 | 2/3 | 0.00 | none / HARD 3: T1 {miss} |" in lines
    assert "| steady | 1 | 3/3 | 1.00 | none |" in lines
    assert "Overall: 5/6 scenario runs passed every hard check (83%)." in lines


def test_an_errored_run_is_a_failed_run_in_the_detail(settings, tmp_path, monkeypatch):
    out = tmp_path / "live-reliability.md"
    monkeypatch.setattr(live_replay, "RELIABILITY", out)
    runs = [[{"name": "broken", "error": "LLMError: boom"}], [run("broken")], [run("broken")]]
    live_replay.write_reliability(runs, settings)
    assert "| broken | 1 | 2/3 | 0.00 | none / HARD 1: LLMError: boom |" in out.read_text(encoding="utf-8")


def test_repeat_must_be_at_least_one(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["live_replay.py", "--repeat", "0", "margaret_happy_path"])
    with pytest.raises(SystemExit) as exit_:
        live_replay.main()  # rejected while parsing, before any settings load or model call
    assert exit_.value.code == 2
