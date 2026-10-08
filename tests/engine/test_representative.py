from app.engine.memory import merge_analysis
from app.engine.state import REP_SLOTS, Session, SlotStatus
from app.llm.schemas import TurnAnalysis


def analysis(**kw) -> TurnAnalysis:
    return TurnAnalysis.model_validate(kw)


# S01-1: capture


def test_representative_details_are_captured_as_provisional_slots():
    s = Session.new()
    s.turn = 1
    changed = merge_analysis(s, analysis(
        representative={"name": "David Chen", "relationship": "son", "policyholder_name": "Margaret Chen"}))
    assert set(changed) == set(REP_SLOTS)
    for name in REP_SLOTS:
        assert s.memory.get(name).status == SlotStatus.PROVISIONAL and s.memory.get(name).source_turn == 1
    assert s.memory.value("rep_name") == "David Chen"
    assert s.memory.value("rep_policyholder_name") == "Margaret Chen"
    assert s.snapshot()["memory"]["rep_name"]["value"] == "David Chen"  # names are not masked
    s.turn = 2
    changed = merge_analysis(s, analysis(representative={"relationship": "daughter"}))
    assert changed == ["rep_relationship"]
    assert s.memory.value("rep_relationship") == "daughter"
    assert s.memory.get("rep_relationship").source_turn == 2
    assert s.memory.value("rep_name") == "David Chen"  # a partial restatement erases nothing
    assert s.memory.value("rep_policyholder_name") == "Margaret Chen"
