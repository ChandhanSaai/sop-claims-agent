import pytest

from tests.replay.runner import assert_turn, load, run_scenario

SCENARIOS = ["margaret_happy_path", "angry_caller"]


@pytest.mark.parametrize("name", SCENARIOS)
def test_golden_transcript(name, settings):
    spec = load(name)
    results = run_scenario(spec, settings)
    for i, (turn_spec, result) in enumerate(zip(spec["turns"], results, strict=True)):
        assert_turn(i, turn_spec, result)
