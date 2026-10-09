from app.engine.machine import Engine
from app.engine.memory import merge_analysis
from app.engine.state import CaseState, Counters, Escalation, Phase, Session, SlotStatus, Verification
from app.llm.schemas import TurnAnalysis


def analysis(**kw) -> TurnAnalysis:
    return TurnAnalysis.model_validate(kw)


def test_capture_any_slot_any_turn_as_provisional():
    s = Session.new()
    s.turn = 1
    changed = merge_analysis(s, analysis(
        identity={"full_name": "Margaret Chen", "dob": "1985-03-15"},
        case_hints={"case_type": "healthcare", "status": "denied", "month": 1},
        intent="denial_question",
    ))
    assert set(changed) == {"full_name", "dob", "case_type", "status_hint", "month", "intent"}
    assert s.memory.get("dob").status == SlotStatus.PROVISIONAL
    assert s.memory.get("dob").source_turn == 1
    assert s.memory.value("intent") == "denial_question"
    assert s.memory.value("month") == "1"


def test_verified_slot_is_not_overwritten_by_a_restated_value():
    s = Session.new()
    s.turn = 1
    merge_analysis(s, analysis(identity={"dob": "1985-03-15"}))
    s.memory.mark_verified(["dob"])
    s.turn = 2
    changed = merge_analysis(s, analysis(identity={"dob": "March 15, 1985"}))  # the same date, restated
    assert changed == []
    assert s.memory.value("dob") == "1985-03-15"


def test_correction_to_verified_identity_resets_verification():
    s = Session.new()
    s.turn = 1
    merge_analysis(s, analysis(identity={"dob": "1985-03-15", "full_name": "Margaret Chen"}))
    s.memory.mark_verified(["dob", "full_name"])
    s.verification = Verification(status="verified", party_id="P9", role="policyholder")
    s.phase = Phase.PROCESS_CASE
    s.case = CaseState(candidates=["CL-2048"], selected_case_id="CL-2048", intent="denial_question")
    s.turn = 2
    changed = merge_analysis(s, analysis(corrections=[{"slot": "dob", "new_value": "1985-03-16"}]))
    assert changed == ["dob"]
    assert s.memory.value("dob") == "1985-03-16"
    assert s.memory.get("dob").status == SlotStatus.PROVISIONAL
    assert s.verification.status == "unverified" and s.verification.party_id is None
    assert s.phase == Phase.VERIFY_ID and s.case == CaseState()  # whoever verifies next re-resolves
    assert s.events[-1].type == "verification_reset"


def test_verification_reset_keeps_the_representative_flag():
    s = Session.new()
    s.memory.set("dob", "1985-03-15", 1)
    s.memory.slots["dob"].status = SlotStatus.VERIFIED
    s.verification = Verification(status="verified", party_id="P9", role="policyholder",
                                  declared_representative=True)
    merge_analysis(s, analysis(corrections=[{"slot": "dob", "new_value": "1986-03-15"}]))
    assert s.verification.status == "unverified" and s.verification.declared_representative


def test_identity_values_given_with_a_correction_replace_the_reset_slots():
    """The live Reader reads "this is actually Ma Tian, born ..., last four ..." as one correction (the name)
    plus identity fields: once the correction resets verification, the rest of the message is the new
    party's identity, not a restatement of the verified slots."""
    s = Session.new()
    s.turn = 1
    merge_analysis(s, analysis(identity={"full_name": "Margaret Chen", "dob": "1985-03-15",
                                         "id_last4": "4472"}))
    s.memory.mark_verified(["full_name", "dob", "id_last4"])
    s.verification = Verification(status="verified", party_id="P9", role="policyholder")
    s.turn = 2
    changed = merge_analysis(s, analysis(
        identity={"full_name": "Ma Tian", "dob": "1964-09-10", "id_last4": "6688"},
        corrections=[{"slot": "full_name", "new_value": "Ma Tian"}]))
    assert set(changed) == {"full_name", "dob", "id_last4"}
    assert {n: s.memory.value(n) for n in ("full_name", "dob", "id_last4")} == {
        "full_name": "Ma Tian", "dob": "1964-09-10", "id_last4": "6688"}
    assert all(s.memory.get(n).status == SlotStatus.PROVISIONAL for n in ("full_name", "dob", "id_last4"))
    assert s.verification.status == "unverified" and s.phase == Phase.VERIFY_ID


def test_verification_reset_drops_the_hand_off_and_the_declined_offer_but_keeps_conduct_counters():
    """A hand-off reference and a declined human offer belong to the party that gave them; off-topic,
    frustration and abuse counts are the conversation's, so a change of name is not a way round them."""
    s = Session.new()
    s.memory.set("dob", "1985-03-15", 1)
    s.memory.slots["dob"].status = SlotStatus.VERIFIED
    s.verification = Verification(status="verified", party_id="P9", role="policyholder")
    s.escalation = Escalation(requested=True, reference="ESC-TEST01", reason="caller asked")
    s.counters = Counters(off_topic=2, frustration_streak=1, gate_explanations=1, abusive=1,
                          human_declined=True, email_offered=True)
    s.turn = 2
    merge_analysis(s, analysis(corrections=[{"slot": "dob", "new_value": "1964-09-10"}]))
    assert s.escalation == Escalation() and not s.counters.human_declined
    assert (s.counters.off_topic, s.counters.frustration_streak, s.counters.abusive) == (2, 1, 1)
    assert s.counters.gate_explanations == 1 and s.counters.email_offered  # cleared only by another party


def test_a_different_person_in_the_identity_fields_is_a_correction_even_when_unlabelled():
    """The live Reader sometimes reads "this is actually Ma Tian, born ..." as identity fields with no
    correction; a different name, birth date or ID for a verified slot resets verification all the same.
    A restatement in another form, a first name alone, or an unreadable date does not."""
    def verified_margaret():
        s = Session.new()
        s.turn = 1
        merge_analysis(s, analysis(identity={"full_name": "Margaret Chen", "dob": "1985-03-15",
                                             "id_last4": "4472"}))
        s.memory.mark_verified(["full_name", "dob", "id_last4"])
        s.verification = Verification(status="verified", party_id="P9", role="policyholder")
        s.turn = 2
        return s

    s = verified_margaret()
    a = analysis(identity={"full_name": "Ma Tian", "dob": "1964-09-10", "id_last4": "6688"})
    changed = merge_analysis(s, a)
    assert [c.slot for c in a.corrections] == ["full_name", "dob", "id_last4"]  # visible in the trace
    assert s.verification.status == "unverified" and s.events[-1].type == "verification_reset"
    assert set(changed) == {"full_name", "dob", "id_last4"} and s.memory.value("full_name") == "Ma Tian"
    for same in ({"full_name": "margaret chen"}, {"full_name": "Margaret"}, {"full_name": "Mrs. Chen"},
                 {"full_name": "Margaret A. Chen"}, {"full_name": "Margaret Ann Chen"},
                 {"full_name": "Margaret C."}, {"full_name": "M. Chen"},
                 {"dob": "March 15, 1985"}, {"dob": "not sure"}, {"dob": "10/09/1985"},  # ambiguous: re-asked
                 {"id_last4": "4472"}, {"phone": "650-000-0000"}, {"email": "other@example.com"}):
        s = verified_margaret()
        a = analysis(identity=same)
        merge_analysis(s, a)
        assert a.corrections == [] and s.verification.status == "verified", same


def test_a_restated_hint_counts_as_given_now():
    s = Session.new()
    assert s.memory.set("case_type", "healthcare", 1)
    assert not s.memory.set("case_type", "healthcare", 3) and s.memory.get("case_type").source_turn == 3


def test_the_next_party_restating_the_earlier_hints_keeps_them(repos, settings):
    """Ma Tian asks about a denied healthcare claim; Margaret takes over and asks about her denied healthcare
    claim from January in the same message: the restated hints are hers too and select CL-2048 at once."""
    eng = Engine(repos, settings)
    s = Session.new()
    eng.greeting(s)
    eng.handle_turn(s, analysis(identity={"full_name": "Ma Tian", "dob": "1964-09-10", "id_last4": "6688"},
                                case_hints={"case_type": "healthcare", "status": "denied"},
                                intent="denial_question"), "Ma Tian, my denied healthcare claim")
    assert s.verification.party_id == "P12"
    eng.handle_turn(s, analysis(identity={"full_name": "Margaret Chen", "dob": "1985-03-15",
                                          "id_last4": "4472"},
                                corrections=[{"slot": "full_name", "new_value": "Margaret Chen"}],
                                case_hints={"case_type": "healthcare", "status": "denied", "month": 1},
                                intent="denial_question"),
                    "Sorry, this is Margaret Chen, 1985-03-15, 4472. Why was my denied healthcare claim from "
                    "January denied?")
    assert s.verification.party_id == "P9" and s.case.selected_case_id == "CL-2048"
    assert s.phase == Phase.PROCESS_CASE and s.case.intent == "denial_question"
