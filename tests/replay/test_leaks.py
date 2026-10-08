import re

import pytest

from app.data.normalize import doc_tokens, normalize_name
from app.data.store import FixtureStore
from app.engine.guard import OutputGuard, contains_token, date_variants
from tests.replay.runner import load, run_scenario, scenario_names


def _token_run(s: str) -> str:
    """Tokens in reading order, space-padded: a phrase leaks only as a contiguous run (the guard's rule)."""
    return f" {' '.join(t for w in normalize_name(s).split() for t in doc_tokens(w))} "


@pytest.mark.parametrize("name", scenario_names())
def test_zero_claim_vocabulary_before_verification(name, settings):
    """Independent of the guard: no claim id, fixture amount, fixture date or non-echoed document phrase
    may appear in any reply of a turn that ends unverified."""
    store = FixtureStore.load(settings.fixtures_dir)
    vocab = OutputGuard(store)
    spec = load(name)
    results = run_scenario(spec, settings)
    said = ""
    for turn_spec, r in zip(spec["turns"], results, strict=True):
        said += " " + turn_spec["user"]
        if r["verified_after"]:
            continue
        reply = r["reply"]
        assert not re.search(r"\bCL-\d+", reply), (name, reply)
        assert not any(a in reply for a in vocab.amounts), (name, reply)
        for d in vocab.dates:
            assert not any(contains_token(reply, v) for v in date_variants(d)[:-1]), (name, reply)
        reply_run, said_run = _token_run(reply), _token_run(said)
        for p in vocab.phrases:
            pr = _token_run(p)
            assert not (pr.strip() and pr in reply_run and pr not in said_run), (name, p, reply)
