import pytest

from app.engine.briefs import render_brief
from app.engine.machine import Engine
from app.engine.memory import merge_analysis
from app.engine.phases import verify_id
from app.engine.phases.verify_id import CONSENT_FACT, NO_AUTHORIZATION, REP_CALL
from app.engine.state import REP_SLOTS, Consent, PendingAsk, Phase, Session, SlotStatus
from app.llm.schemas import TurnAnalysis
from tests.engine.helpers import TODAY, turn

DAVID = {"name": "David Chen", "relationship": "son", "policyholder_name": "Margaret Chen"}
MARGARET = {"full_name": "Margaret Chen", "policy_number": "POL-9921", "dob": "1985-03-15",
            "id_last4": "4472"}
HINTS = {"case_type": "healthcare", "status": "denied", "month": 1}


def analysis(**kw) -> TurnAnalysis:
    return TurnAnalysis.model_validate(kw)


def declare(s, repos, settings, **fields):
    return turn(s, repos, settings, verify_id.handle, **fields)


@pytest.fixture
def no_policyholder_lookup(repos, monkeypatch):
    """The policyholder path must never run for a declared representative."""
    def boom(*args, **kwargs):
        raise AssertionError("policyholder find/verify called on the representative path")
    monkeypatch.setattr(repos.policyholders, "find", boom)
    monkeypatch.setattr(repos.policyholders, "verify", boom)


# S01-1: capture


def test_representative_details_are_captured_as_provisional_slots():
    s = Session.new()
    s.turn = 1
    changed = merge_analysis(s, analysis(representative=DAVID))
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


# S01-2: the sub-flow in VERIFY_ID


def test_declaration_collects_the_missing_representative_fields(repos, settings, no_policyholder_lookup):
    s = Session.new()
    r = declare(s, repos, settings, caller_role="representative", case_hints={"status": "denied"})
    assert s.verification.declared_representative and s.verification.status == "unverified"
    assert s.pending_ask == PendingAsk.IDENTITY_FIELDS and s.consent.status == "none"
    text = " ".join(r.brief.must_say)
    assert "noted" in text and "your full name" in text and "relationship" in text
    assert "the policyholder's full name" in text
    assert r.brief.allowed_facts == {} and r.brief.ask and not r.advanced
    assert any("Do not confirm or deny" in m for m in r.brief.must_not)
    r2 = declare(s, repos, settings, representative={"relationship": "son"})
    text2 = " ".join(r2.brief.must_say)
    assert "your full name" in text2 and "the policyholder's full name" in text2
    assert "relationship" not in text2
    assert s.consent.status == "none" and s.pending_ask == PendingAsk.IDENTITY_FIELDS


def test_an_approved_consent_in_verify_id_is_treated_as_verified(repos, settings):
    s = Session.new()
    s.verification.declared_representative = True
    s.consent = Consent(status="approved", party_id="P9", consent_id="CON-0007",
                        representative_name="David Chen")
    r = declare(s, repos, settings)
    v = s.verification
    assert (v.status, v.role, v.party_id) == ("verified", "representative", "P9")
    assert s.phase == Phase.RESOLVE_INTENT and r.advanced and not r.needs_input
    assert r.transition_fact == CONSENT_FACT and r.transition_facts == {"consent_reference": "CON-0007"}


def test_no_match_wording_is_identical_for_a_wrong_representative_or_policyholder_name(repos, settings):
    wrong_rep, wrong_holder = Session.new(), Session.new()
    a = declare(wrong_rep, repos, settings, caller_role="representative",
                representative={**DAVID, "name": "Nobody Here"})
    b = declare(wrong_holder, repos, settings, caller_role="representative",
                representative={**DAVID, "policyholder_name": "Nobody Here"})
    assert a.brief.must_say == b.brief.must_say == [NO_AUTHORIZATION] and a.brief.ask == b.brief.ask
    assert a.brief.offer_human and wrong_rep.pending_ask == PendingAsk.HUMAN_OFFER
    for s in (wrong_rep, wrong_holder):
        assert s.consent.status == "none" and s.consent.consent_id is None and s.verification.attempts == 0
        assert s.verification.status == "unverified"
    assert "Margaret" not in NO_AUTHORIZATION and "Nobody" not in NO_AUTHORIZATION


def test_same_failed_triple_is_not_retried_but_a_changed_one_is(repos, settings, monkeypatch):
    calls = []
    real = repos.representatives.match
    monkeypatch.setattr(repos.representatives, "match", lambda *a: calls.append(a) or real(*a))
    s = Session.new()
    declare(s, repos, settings, caller_role="representative", representative={**DAVID, "name": "Nobody Here"})
    s.counters.human_declined = True  # the caller said no to the offer
    r = declare(s, repos, settings, representative={"name": "nobody  HERE"})  # format-only restatement
    assert len(calls) == 1 and r.brief.must_say == [NO_AUTHORIZATION]
    assert not r.brief.offer_human and r.brief.ask is None and s.pending_ask == PendingAsk.NONE
    declare(s, repos, settings, representative={"name": "David Chen"})
    assert len(calls) == 2 and s.consent.status == "pending" and s.consent.consent_id == "CON-0001"


def test_match_requests_consent_once_and_waits(repos, settings, no_policyholder_lookup):
    s = Session.new()
    r = declare(s, repos, settings, caller_role="representative", representative=DAVID, identity=MARGARET,
                case_hints=HINTS, intent="denial_question")
    c = s.consent
    assert (c.status, c.consent_id, c.party_id, c.representative_name, c.polls) == (
        "pending", "CON-0001", "P9", "David Chen", 0)
    assert s.pending_ask == PendingAsk.CONSENT_WAIT and s.verification.status == "unverified"
    assert s.events[-1].type == "consent_requested"
    assert s.events[-1].data == {"consent_id": "CON-0001", "scenario": "default"}
    text = render_brief(r.brief)
    assert "consent" in text and "noted" in text and "representative" in text
    assert r.brief.allowed_facts == {} and not r.advanced
    assert "CL-" not in text and "margaret@email.com" not in text and "POL-9921" not in text
    assert any("contact" in m for m in r.brief.must_not)
    assert all(s.memory.get(n).status == SlotStatus.PROVISIONAL for n in (*REP_SLOTS, *MARGARET))


def test_default_scenario_polls_once_per_turn_then_approves_and_chains_to_the_claim(
        repos, settings, no_policyholder_lookup):
    eng = Engine(repos, settings, lambda: TODAY)
    s = Session.new()
    eng.greeting(s)
    eng.handle_turn(s, analysis(caller_role="representative", representative=DAVID,
                                identity={"policy_number": "POL-9921"}, case_hints=HINTS,
                                intent="denial_question"), "David Chen here, calling for my mother")
    assert s.consent.polls == 0 and s.pending_ask == PendingAsk.CONSENT_WAIT
    b2 = eng.handle_turn(s, analysis(), "Has she approved it yet?")
    assert s.consent.polls == 1 and s.consent.status == "pending" and s.phase == Phase.VERIFY_ID
    assert s.pending_ask == PendingAsk.CONSENT_WAIT and b2.allowed_facts == {}
    assert "CL-" not in render_brief(b2) and "consent" in render_brief(b2)
    b3 = eng.handle_turn(s, analysis(), "Anything now?")
    v = s.verification
    assert s.consent.polls == 2 and s.consent.status == "approved"
    assert (v.status, v.role, v.party_id) == ("verified", "representative", "P9")
    assert s.phase == Phase.PROCESS_CASE and s.case.selected_case_id == "CL-2048"
    assert s.pending_ask == PendingAsk.ANYTHING_ELSE
    assert b3.must_say[0] == CONSENT_FACT and b3.allowed_facts["consent_reference"] == "CON-0001"
    assert b3.allowed_facts["claim_id"] == "CL-2048"
    assert "pathology report" in b3.allowed_facts["documents_needed"]
    assert all(s.memory.get(n).status == SlotStatus.VERIFIED for n in REP_SLOTS)
    assert s.memory.get("policy_number").status == SlotStatus.PROVISIONAL
    consent_events = [e.type for e in s.events if e.type.startswith("consent_")]
    assert consent_events == ["consent_requested", "consent_approved"]
    assert s.consent.consent_id == "CON-0001"


def test_timeout_scenario_stays_pending_for_five_polls_then_times_out_and_never_re_requests(
        repos, settings, monkeypatch):
    requests = []
    real = repos.consent.request
    monkeypatch.setattr(repos.consent, "request", lambda *a: requests.append(a) or real(*a))
    s = Session.new("timeout")
    declare(s, repos, settings, caller_role="representative", representative=DAVID)
    assert len(requests) == 1 and s.consent.status == "pending"
    for i in range(1, 6):
        r = declare(s, repos, settings)
        assert (s.consent.polls, s.consent.status) == (i, "pending"), i
        assert s.pending_ask == PendingAsk.CONSENT_WAIT
        assert not r.brief.offer_human and r.brief.allowed_facts == {}
    r = declare(s, repos, settings)
    assert s.consent.polls == 6 and s.consent.status == "timed_out" and s.verification.status == "unverified"
    assert r.brief.offer_human and s.pending_ask == PendingAsk.HUMAN_OFFER and r.brief.allowed_facts == {}
    assert s.events[-1].type == "consent_timed_out" and "representative" in " ".join(r.brief.must_say)
    s.counters.human_declined = True
    r2 = declare(s, repos, settings, representative=DAVID)  # restating the details does not re-request
    assert len(requests) == 1 and s.consent.polls == 6 and s.consent.status == "timed_out"
    assert not r2.brief.offer_human and r2.brief.ask is None and s.pending_ask == PendingAsk.NONE
    assert any("consent" in m.lower() for m in r2.brief.must_say)


def test_policyholder_claim_after_a_declaration_keeps_the_representative_path(
        repos, settings, no_policyholder_lookup):
    s = Session.new()
    declare(s, repos, settings, caller_role="representative", representative=DAVID)
    r = declare(s, repos, settings, caller_role="policyholder", identity=MARGARET)
    assert s.verification.status == "unverified" and s.verification.declared_representative
    assert s.consent.polls == 1 and s.pending_ask == PendingAsk.CONSENT_WAIT
    assert r.brief.must_say[0] == REP_CALL and r.brief.allowed_facts == {}


def test_policyholder_who_mentions_a_helper_keeps_the_policyholder_path(repos, settings):
    s = Session.new()
    declare(s, repos, settings, caller_role="policyholder", identity=MARGARET,
            representative={"name": "David Chen", "relationship": "son"})
    assert not s.verification.declared_representative and s.consent.status == "none"
    assert (s.verification.status, s.verification.role) == ("verified", "policyholder")
    unknown = Session.new()  # role unknown plus a representative field: the representative path
    declare(unknown, repos, settings, representative={"relationship": "son"})
    assert unknown.verification.declared_representative


def test_timeout_after_an_escalation_does_not_offer_a_human_again(repos, settings):
    eng = Engine(repos, settings, lambda: TODAY)
    s = Session.new("timeout")
    eng.greeting(s)
    eng.handle_turn(s, analysis(caller_role="representative", representative=DAVID), "for my mother")
    eng.handle_turn(s, analysis(requests={"wants_human": True}), "get me a person")
    assert s.escalation.requested and s.consent.status == "pending"
    for _ in range(5):
        eng.handle_turn(s, analysis(), "still waiting?")
    b = eng.handle_turn(s, analysis(), "anything?")
    assert s.consent.status == "timed_out" and not b.offer_human and b.ask is None
    assert s.pending_ask == PendingAsk.NONE


def test_identifiers_given_by_a_representative_never_verify_them_as_the_policyholder(
        repos, settings, no_policyholder_lookup):
    eng = Engine(repos, settings, lambda: TODAY)
    s = Session.new("timeout")
    eng.greeting(s)
    eng.handle_turn(s, analysis(caller_role="representative", representative=DAVID, identity=MARGARET),
                    "David Chen calling for my mother Margaret Chen, POL-9921, DOB 1985-03-15, SSN 4472")
    b = eng.handle_turn(s, analysis(identity={"phone": "650-521-2836", "email": "margaret@email.com"}),
                        "her phone is 650-521-2836 and email margaret@email.com")
    assert s.verification.status == "unverified" and s.verification.attempts == 0
    assert s.phase == Phase.VERIFY_ID and s.verification.last_fingerprint is None
    assert all(s.memory.get(n).status == SlotStatus.PROVISIONAL for n in (*MARGARET, "phone", "email"))
    assert b.allowed_facts == {} and "CL-" not in render_brief(b)
