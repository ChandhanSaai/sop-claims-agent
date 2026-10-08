from datetime import date

import pytest

from app.data.repos import ConsentService, EmailOutbox


def test_store_loads_all_fixtures(store):
    assert len(store.policyholders) == 4
    assert len(store.claims) == 5
    assert store.representatives[0].rep_name == "David Chen"
    assert store.consent_scenarios["timeout"].status_sequence == ["pending"] * 5
    assert store.policyholders[0].dob == date(1985, 3, 15)


def test_find_by_each_unique_key(repos):
    p = repos.policyholders
    assert [r.party_id for r in p.find(policy_number="pol-9921")] == ["P9"]
    assert [r.party_id for r in p.find(phone="650-521-2836")] == ["P9"]
    assert [r.party_id for r in p.find(phone="650-521-2830")] == ["P13"]  # one digit apart from P9
    assert [r.party_id for r in p.find(email="YAWEN.LI@example.com")] == ["P13"]  # alias
    assert [r.party_id for r in p.find(name="yaven li")] == ["P13"]  # name alias
    assert p.find(name="Nobody Here") == []


def test_find_falls_through_keys_that_miss(repos):
    p = repos.policyholders
    assert [r.party_id for r in p.find(phone="650-000-0000", name="Margaret Chen")] == ["P9"]
    assert [r.party_id for r in p.find(policy_number="POL-0000", email="margaret@email.com")] == ["P9"]
    assert p.find(policy_number="POL-0000", phone="650-000-0000", email="no@x.com", name="Nobody") == []


def test_find_returns_every_key_match_in_record_order(repos):
    p = repos.policyholders
    # P13's phone is one digit from Margaret's: both records are candidates, verify picks between them
    assert [r.party_id for r in p.find(phone="650-521-2830", name="Margaret Chen")] == ["P9", "P13"]
    assert [r.party_id for r in p.find(phone="650-521-2836", name="Margaret Chen")] == ["P9"]  # no duplicate


def test_verify_three_of_five_and_strong_field(repos):
    p = repos.policyholders
    rec = p.get("P9")
    ok = p.verify(
        rec,
        {"full_name": "margaret chen", "dob": "1985-03-15", "id_last4": "4472"},
        min_fields=3,
        require_strong=False,
    )
    assert ok.passed and ok.matched_count == 3
    weak = p.verify(
        rec,
        {"full_name": "Margaret Chen", "phone": "6505212836", "email": "margaret@email.com"},
        min_fields=3,
        require_strong=False,
    )
    assert weak.passed
    strict = p.verify(
        rec,
        {"full_name": "Margaret Chen", "phone": "6505212836", "email": "margaret@email.com"},
        min_fields=3,
        require_strong=True,
    )
    assert not strict.passed and strict.matched_count == 3
    near_miss = p.verify(
        rec,
        {"full_name": "Margaret Chen", "dob": "1985-03-15", "phone": "650-521-2830"},
        min_fields=3,
        require_strong=False,
    )
    assert not near_miss.passed and near_miss.matched_count == 2
    extra_wrong = p.verify(
        rec,
        {"full_name": "Margaret Chen", "dob": "1985-03-15", "phone": "650-000-0000", "id_last4": "4472"},
        min_fields=3,
        require_strong=False,
    )
    assert extra_wrong.passed and extra_wrong.matched_count == 3
    assert extra_wrong.matched == ["full_name", "dob", "id_last4"]


def test_policy_number_never_counts(repos):
    p = repos.policyholders
    rec = p.get("P9")
    r = p.verify(
        rec,
        {"policy_number": "POL-9921", "dob": "1985-03-15", "id_last4": "4472"},
        min_fields=3,
        require_strong=False,
    )
    assert not r.passed and r.matched_count == 2


def test_claims_filter_and_decoy(repos):
    c = repos.claims
    mine = c.for_party("P9")
    assert len(mine) == 4
    january = c.filter(mine, case_type="healthcare", month=1)
    assert {x.case_id for x in january} == {"CL-2048", "CL-2011"}
    denied = c.filter(mine, case_type="healthcare", status="denied", month=1)
    assert [x.case_id for x in denied] == ["CL-2048"]
    assert [x.case_id for x in c.filter(mine, case_type="HEALTHCARE", status="Denied")] == ["CL-2048"]
    assert c.filter(mine, year=2025, case_type="healthcare")[0].case_id == "CL-2011"
    assert c.get("CL-2102").documents_needed == []
    assert c.get("CL-2048").allowed_max_amount == "1450.00"


def test_representatives_and_consent(store, repos):
    assert repos.representatives.match("david chen", "Margaret Chen").buyer_party_id == "P9"
    assert repos.representatives.match("David Chen", "Ava Lopez") is None
    svc = ConsentService(store.consent_scenarios)
    cid = svc.request("P9", "David Chen", "default")
    assert svc.poll(cid) == "pending"
    assert svc.poll(cid) == "approved"
    assert [svc.poll(cid) for _ in range(3)] == ["approved"] * 3  # approval is final
    cid2 = svc.request("P9", "David Chen", "timeout")
    assert [svc.poll(cid2) for _ in range(5)] == ["pending"] * 5
    assert svc.poll(cid2) == "timed_out"


def test_outbox_masks_and_records(tmp_path):
    box = EmailOutbox(tmp_path / "outbox.jsonl")
    rec = box.send("margaret@email.com", "Summary", "body text")
    assert rec.id == "EML-0001"
    assert rec.to_masked == "m*******@email.com"
    assert box.list()[0].body == "body text"
    assert (tmp_path / "outbox.jsonl").read_text().count("\n") == 1
    assert "margaret@email.com" not in (tmp_path / "outbox.jsonl").read_text()
    box.send("ava.lopez@email.com", "Second", "body two")
    assert (tmp_path / "outbox.jsonl").read_text().count("\n") == 2


def test_unknown_party_raises(repos):
    with pytest.raises(KeyError):
        repos.policyholders.get("P404")


def test_outbox_keeps_the_record_when_the_file_cannot_be_written(tmp_path):
    (tmp_path / "blocked").write_text("not a directory")
    box = EmailOutbox(tmp_path / "blocked" / "outbox.jsonl")  # mkdir raises an OSError
    rec = box.send("margaret@email.com", "Summary", "body")
    assert rec.id == "EML-0001" and box.list() == [rec]


def test_find_by_policy_digits_alone(repos):
    assert [r.party_id for r in repos.policyholders.find(policy_number="9921")] == ["P9"]
    assert [r.party_id for r in repos.policyholders.find(policy_number="pol 9921")] == ["P9"]
    assert repos.policyholders.find(policy_number="992") == []
    assert repos.policyholders.find(policy_number="19921") == []  # digits must match exactly
