from app.engine.state import Session

MASKED = {"dob": "1985-03-15", "phone": "650-521-2836", "email": "margaret@email.com",
          "id_last4": "4472", "policy_number": "POL-9921",
          "free_text": "call me back at 650-521-2836"}  # the caller's own words may hold an identifier
VISIBLE = {"full_name": "Margaret Chen", "case_type": "healthcare", "status_hint": "denied", "month": "1",
           "year": "2026", "case_id": "CL-2048", "intent": "denial_question"}


def test_snapshot_masks_identifiers_and_omits_fingerprint():
    s = Session(id="s1", created_at="t0")  # fixed, so the leak check below cannot match a random id
    for name, val in {**MASKED, **VISIBLE}.items():
        s.memory.set(name, val, 1)
    s.verification.last_fingerprint = "f" * 64
    snap = s.snapshot()
    assert {n: snap["memory"][n]["value"] for n in MASKED} == dict.fromkeys(MASKED, "******")
    assert {n: snap["memory"][n]["value"] for n in VISIBLE} == VISIBLE
    assert "last_fingerprint" not in snap["verification"]
    assert not any(v in repr(snap) for v in [*MASKED.values(), "f" * 64])


def test_snapshot_carries_the_fence_turn():
    s = Session.new()
    assert s.snapshot()["fence_turn"] == 0
    s.fence_turn = 3
    assert s.snapshot()["fence_turn"] == 3


def test_snapshot_events_start_at_the_fence():
    s = Session.new()
    s.turn = 1
    s.log("verified", party_id="P9")
    s.turn = 2
    s.log("verification_reset", party_id="P9")
    s.fence_turn = 2
    s.turn = 3
    s.log("verified", party_id="P12")
    events = [(e["turn"], e["type"]) for e in s.snapshot()["events"]]
    assert events == [(2, "verification_reset"), (3, "verified")]


def test_snapshot_hides_the_earlier_partys_hints_and_reset_details():
    s = Session.new()
    s.turn = 1
    s.memory.set("case_id", "CL-2048", 1)
    s.turn = 2
    s.log("verification_reset", slot="full_name", party_id="P9", caller="policyholder:P9",
          escalation={"requested": True, "reference": "ESC-ABC123", "reason": "x"}, human_declined=True)
    s.fence_turn = 2
    s.memory.set("case_type", "dental", 2)
    snap = s.snapshot()
    assert "case_id" not in snap["memory"] and "case_type" in snap["memory"]
    assert snap["events"][-1] == {"turn": 2, "type": "verification_reset", "data": {"slot": "full_name"}}
    assert "ESC-ABC123" not in repr(snap) and "P9" not in repr(snap["events"])
