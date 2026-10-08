from app.engine.memory import merge_analysis
from app.engine.state import CaseState, Phase, Session, SlotStatus, Verification
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
    changed = merge_analysis(s, analysis(identity={"dob": "1990-01-01"}))
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
    merge_analysis(s, TurnAnalysis.model_validate(
        {"corrections": [{"slot": "dob", "old_value": "1985-03-15", "new_value": "1986-03-15"}]}))
    assert s.verification.status == "unverified" and s.verification.declared_representative
