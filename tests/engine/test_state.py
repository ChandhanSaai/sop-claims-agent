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
