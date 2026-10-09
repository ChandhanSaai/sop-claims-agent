from datetime import date

import pytest

from app.engine.briefs import render_brief
from app.engine.context import resolve_pending
from app.engine.guard import contains_token, date_variants
from app.engine.machine import Engine
from app.engine.memory import merge_analysis
from app.engine.phases.post_process import GOODBYE
from app.engine.phases.verify_id import GENERIC_FAIL, MEANWHILE, handle
from app.engine.state import PendingAsk, Phase, Session, SlotStatus
from app.llm.schemas import TurnAnalysis

TODAY = date(2026, 10, 7)


def run(session, repos, settings, **analysis_fields):
    analysis = TurnAnalysis.model_validate(analysis_fields)
    session.turn += 1
    ctx = resolve_pending(session, analysis, "")
    ctx.changed_slots = merge_analysis(session, analysis)
    return handle(session, ctx, repos, settings, TODAY)


def test_margaret_verifies_in_one_turn_and_advances(repos, settings):
    s = Session.new()
    r = run(s, repos, settings,
            identity={"full_name": "Margaret Chen", "policy_number": "POL-9921", "dob": "1985-03-15",
                      "id_last4": "4472"},
            caller_role="policyholder",
            case_hints={"case_type": "healthcare", "status": "denied", "month": 1})
    assert s.verification.status == "verified" and s.verification.party_id == "P9"
    assert s.phase == Phase.RESOLVE_INTENT
    assert r.advanced and not r.needs_input
    assert "complete" in r.transition_fact
    assert s.memory.get("dob").status == SlotStatus.VERIFIED
    assert s.verification.attempts == 0


def test_name_only_asks_for_more_without_counting_or_confirming(repos, settings):
    s = Session.new()
    r = run(s, repos, settings, identity={"full_name": "Margaret Chen"}, case_hints={"status": "denied"})
    assert s.phase == Phase.VERIFY_ID and s.verification.attempts == 0
    assert s.pending_ask == PendingAsk.IDENTITY_FIELDS
    text = " ".join(r.brief.must_say)
    assert "date of birth" in text and "noted" in text
    assert any("Do not confirm or deny" in m for m in r.brief.must_not)
    assert r.brief.allowed_facts == {}


def test_identity_ask_asks_only_for_what_is_still_missing(repos, settings):
    nothing = " ".join(run(Session.new(), repos, settings).brief.must_say)
    assert "at least three of these" in nothing and "your full name" in nothing
    name_only = " ".join(run(Session.new(), repos, settings,
                             identity={"full_name": "Margaret Chen"}).brief.must_say)
    assert "at least two more of these" in name_only and "full name" not in name_only
    assert "date of birth" in name_only and "policy number" in name_only
    two = " ".join(run(Session.new(), repos, settings,
                       identity={"full_name": "Margaret Chen", "dob": "1985-03-15"}).brief.must_say)
    assert "at least one more of these: the phone number on file, the email on file, or the last four" in two


def test_unknown_name_gets_identical_wording(repos, settings):
    known, unknown = Session.new(), Session.new()
    a = run(known, repos, settings, identity={"full_name": "Margaret Chen"})
    b = run(unknown, repos, settings, identity={"full_name": "Nobody Here"})
    assert a.brief.must_say == b.brief.must_say and a.brief.ask == b.brief.ask
    assert unknown.verification.attempts == 0


def test_policy_number_does_not_count(repos, settings):
    s = Session.new()
    r = run(s, repos, settings,
            identity={"policy_number": "POL-9921", "dob": "1985-03-15", "id_last4": "4472"})
    assert s.verification.status == "unverified" and s.verification.attempts == 0
    assert s.pending_ask == PendingAsk.IDENTITY_FIELDS
    assert r.brief.ask


def test_lookup_by_phone_without_name(repos, settings):
    s = Session.new()
    run(s, repos, settings,
        identity={"phone": "650-521-2836", "email": "margaret@email.com", "dob": "1985-03-15"})
    assert s.verification.status == "verified" and s.verification.party_id == "P9"


def test_failed_attempts_are_generic_and_exhaust_at_three(repos, settings):
    s = Session.new()
    bad = {"full_name": "Margaret Chen", "dob": "1985-03-15", "phone": "650-521-2830"}  # near miss phone
    r1 = run(s, repos, settings, identity=bad)
    assert s.verification.attempts == 1 and GENERIC_FAIL in r1.brief.must_say
    assert not any("phone" in m.lower() for m in r1.brief.must_say[:1])
    r_same = run(s, repos, settings, identity=bad)  # same identifiers again: no new attempt
    assert s.verification.attempts == 1 and r_same.brief.ask
    run(s, repos, settings, identity={"phone": "650-521-2831"})
    assert s.verification.attempts == 2
    r3 = run(s, repos, settings, identity={"phone": "650-521-2832"})
    assert s.verification.attempts == 3 and s.verification.status == "exhausted"
    assert r3.brief.offer_human and s.pending_ask == PendingAsk.HUMAN_OFFER
    r4 = run(s, repos, settings, identity={"phone": "650-521-2836"})  # correct now, but KBA is over
    assert s.verification.status == "exhausted" and r4.brief.offer_human


def test_format_only_restatement_is_not_a_new_attempt(repos, settings):
    s = Session.new()
    run(s, repos, settings,
        identity={"full_name": "Margaret Chen", "dob": "1985-03-15", "phone": "650-521-2830"})
    assert s.verification.attempts == 1
    r = run(s, repos, settings,
            identity={"full_name": "margaret chen", "dob": "March 15, 1985", "phone": "(650) 521-2830"})
    assert s.verification.attempts == 1 and GENERIC_FAIL in r.brief.must_say


def test_pass_marks_only_matched_identifiers_verified(repos, settings):
    s = Session.new()
    run(s, repos, settings,  # the wrong phone matches no record, so lookup falls through to the name
        identity={"full_name": "Margaret Chen", "dob": "1985-03-15", "id_last4": "4472",
                  "phone": "650-000-0000"})
    assert s.verification.status == "verified" and s.verification.party_id == "P9"
    assert s.memory.get("phone").status == SlotStatus.PROVISIONAL
    assert all(s.memory.get(n).status == SlotStatus.VERIFIED for n in ("full_name", "dob", "id_last4"))


def test_lookup_miss_with_three_fields_costs_one_attempt(repos, settings):
    s = Session.new()
    run(s, repos, settings, identity={"full_name": "Nobody Here", "dob": "1985-03-15", "id_last4": "4472"})
    assert s.verification.attempts == 1 and s.verification.status == "unverified"


@pytest.mark.parametrize("dob", ["03/05/1985", "sometime in spring 85"])  # ambiguous, unparseable
def test_ambiguous_or_unparseable_dob_is_reasked_not_counted(repos, settings, dob):
    s = Session.new()
    r = run(s, repos, settings,
            identity={"full_name": "Margaret Chen", "dob": dob, "id_last4": "4472"})
    assert s.pending_ask == PendingAsk.DOB_FORMAT and s.verification.attempts == 0
    assert "month" in r.brief.ask.lower()


def test_dob_reask_example_is_not_a_fixture_dob(repos, settings):
    s = Session.new()
    r = run(s, repos, settings, identity={"full_name": "Margaret Chen", "dob": "03/05/1985"})
    assert s.pending_ask == PendingAsk.DOB_FORMAT
    for holder in repos.store.policyholders:
        assert not any(contains_token(r.brief.ask, v) for v in date_variants(holder.dob)), holder.party_id


def test_gate_explained_at_most_twice_when_frustrated(repos, settings):
    s = Session.new()
    for i in range(3):
        analysis = TurnAnalysis.model_validate({"identity": {"full_name": "Margaret Chen"},
                                                "affect": {"frustration": 3, "refusal": True}})
        s.turn += 1
        ctx = resolve_pending(s, analysis, "")
        ctx.changed_slots = merge_analysis(s, analysis)
        ctx.tone = "de_escalate"
        r = handle(s, ctx, repos, settings, TODAY)
        explained = any("protect" in m for m in r.brief.must_say)
        assert explained == (i < 2)
        assert (MEANWHILE in r.brief.must_say) == (i < 2)  # what the assistant can do in the meantime
    assert s.counters.gate_explanations == 2


def test_goodbye_before_verification_is_a_short_goodbye(repos, settings):
    s = Session.new()
    run(s, repos, settings, identity={"full_name": "Margaret Chen"})
    assert s.pending_ask == PendingAsk.IDENTITY_FIELDS
    r = run(s, repos, settings, requests={"closing": True})
    assert r.brief.must_say == [GOODBYE] and r.brief.ask is None and r.brief.allowed_facts == {}
    assert s.phase == Phase.VERIFY_ID and s.pending_ask == PendingAsk.IDENTITY_FIELDS
    assert s.verification.attempts == 0
    r2 = run(s, repos, settings, requests={"closing": True}, identity={"dob": "1985-03-15"})  # new field
    assert r2.brief.ask and GOODBYE not in r2.brief.must_say


def test_declined_human_offer_is_not_repeated_after_exhaustion(repos, settings):
    eng = Engine(repos, settings, lambda: TODAY)
    s = Session.new()
    eng.greeting(s)
    for phone in ("650-521-2830", "650-521-2831", "650-521-2832"):
        b = eng.handle_turn(s, TurnAnalysis.model_validate(
            {"identity": {"full_name": "Margaret Chen", "dob": "1985-03-15", "phone": phone}}), phone)
    assert s.verification.status == "exhausted" and b.offer_human and s.pending_ask == PendingAsk.HUMAN_OFFER
    b_no = eng.handle_turn(s, TurnAnalysis.model_validate({"requests": {"confirmation": "no"}}), "no")
    assert s.counters.human_declined and not b_no.offer_human and b_no.ask is None
    assert s.pending_ask == PendingAsk.NONE
    b_more = eng.handle_turn(s, TurnAnalysis.model_validate({"identity": {"email": "margaret@email.com"}}),
                             "margaret@email.com")
    assert s.verification.status == "exhausted" and not b_more.offer_human and b_more.ask is None
    assert s.pending_ask == PendingAsk.NONE and any("representative" in m for m in b_more.must_say)


def test_declared_representative_is_not_verified_as_the_policyholder_next_turn(repos, settings):
    eng = Engine(repos, settings, lambda: TODAY)
    s = Session.new()
    eng.greeting(s)
    eng.handle_turn(s, TurnAnalysis.model_validate({
        "caller_role": "representative",
        "representative": {"name": "David Chen", "relationship": "son", "policyholder_name": "Margaret Chen"},
        "identity": {"full_name": "Margaret Chen", "policy_number": "POL-9921", "dob": "1985-03-15"},
    }), "I'm David Chen calling for my mother Margaret Chen, POL-9921, her DOB is 1985-03-15")
    assert s.pending_ask == PendingAsk.CONSENT_WAIT and s.verification.declared_representative
    # the Reader reads the follow-up as role unknown; the declaration sticks and the identifiers do not verify
    b2 = eng.handle_turn(s, TurnAnalysis.model_validate({"identity": {"id_last4": "4472"},
                                                         "requests": {"confirmation": "no"}}),
                         "no, her SSN last four is 4472")
    assert s.verification.status == "unverified" and s.phase == Phase.VERIFY_ID
    assert b2.allowed_facts == {} and "CL-" not in render_brief(b2)
    assert any("consent" in m for m in b2.must_say)


def test_wrong_phone_of_another_record_does_not_hide_the_right_one(repos, settings):
    s = Session.new()
    run(s, repos, settings,  # P13's phone is one digit from Margaret's: both are candidates
        identity={"full_name": "Margaret Chen", "dob": "1985-03-15", "phone": "650-521-2830"})
    assert s.verification.attempts == 1 and s.verification.status == "unverified"
    run(s, repos, settings, identity={"email": "margaret@email.com"})
    assert s.verification.status == "verified" and s.verification.party_id == "P9"
    assert s.verification.attempts == 1 and s.memory.get("phone").status == SlotStatus.PROVISIONAL


@pytest.mark.parametrize("strong", [False, True])
def test_strict_flag_requires_dob_or_id4(repos, settings, strong):
    settings.verify_require_strong_field = strong
    s = Session.new()
    run(s, repos, settings,
        identity={"full_name": "Margaret Chen", "phone": "650-521-2836", "email": "margaret@email.com"})
    assert (s.verification.status == "verified") is (not strong)
