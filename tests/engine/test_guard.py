from app.engine.guard import OutputGuard
from app.engine.state import Escalation, Session, Turn, Verification
from app.llm.schemas import ReplyBrief


def unverified(store, user_said="I'm calling about my denied healthcare claim from January"):
    s = Session.new()
    s.turn = 1
    s.memory.set("dob", "1985-03-15", 1)
    s.memory.set("phone", "650-521-2836", 1)
    s.memory.set("email", "margaret@email.com", 1)
    s.memory.set("id_last4", "4472", 1)
    s.memory.set("policy_number", "POL-9921", 1)
    s.transcript.append(Turn(role="user", text=user_said))
    return s


def test_pre_verification_blocks_claim_facts_but_allows_echo(store):
    g = OutputGuard(store)
    s = unverified(store)
    brief = ReplyBrief(phase="VERIFY_ID", goal="g")
    assert g.check("I've noted the denied healthcare claim from January you mentioned. "
                   "Could you share your date of birth?", s, brief).ok
    assert not g.check("Your claim CL-2048 was denied.", s, brief).ok
    assert not g.check("That claim was denied on January 12, 2026.", s, brief).ok
    assert not g.check("The review file did not include the pathology report and office note.", s, brief).ok
    assert not g.check("The allowed amount is 1450.00.", s, brief).ok


def test_identifiers_are_never_echoed(store):
    g = OutputGuard(store)
    s = unverified(store)
    brief = ReplyBrief(phase="VERIFY_ID", goal="g")
    for leak in ("born on 1985-03-15", "born March 15, 1985", "number 650-521-2836", "number (650) 521 2836",
                 "email margaret@email.com", "ending in 4472", "policy POL-9921"):
        r = g.check(f"Thanks, {leak}.", s, brief)
        assert not r.ok and any(v.startswith("identifier:") for v in r.violations), leak


def test_echoed_document_name_is_allowed_only_when_caller_said_it(store):
    g = OutputGuard(store)
    s = unverified(store, user_said="they said my pathology report was missing")
    brief = ReplyBrief(phase="VERIFY_ID", goal="g")
    assert g.check("I've noted the pathology report issue; first I need to verify you.", s, brief).ok
    assert not g.check("I've noted the office note issue.", s, brief).ok


def test_verified_replies_must_stay_inside_allowed_facts(store):
    g = OutputGuard(store)
    s = unverified(store)
    s.verification = Verification(status="verified", party_id="P9", role="policyholder")
    brief = ReplyBrief(phase="PROCESS_CASE", goal="g", allowed_facts={
        "claim_id": "CL-2048", "claim_opened": "January 12, 2026", "appeal_deadline": "March 18, 2026",
        "allowed_max_amount": "$1450.00",
    })
    assert g.check("Claim CL-2048 from January 2026 was denied; the appeal deadline was March 18, 2026.",
                   s, brief).ok
    assert not g.check("Your other claim CL-2011 was closed.", s, brief).ok
    assert not g.check("It was opened on February 28, 2026.", s, brief).ok
    assert not g.check("The allowed amount is 3500.00.", s, brief).ok
    assert g.check("The allowed amount is 1450.00.", s, brief).ok


def test_markup_and_internal_tags_are_rejected(store):
    g = OutputGuard(store)
    s = unverified(store)
    r = g.check("<thinking>hmm</thinking> Could you share your date of birth?", s,
                ReplyBrief(phase="p", goal="g"))
    assert not r.ok and "markup_tag" in r.violations


def test_escalation_reference_is_allowed_when_it_matches(store):
    g = OutputGuard(store)
    s = unverified(store)
    s.verification = Verification(status="verified", party_id="P9", role="policyholder")
    s.escalation = Escalation(requested=True, reference="ESC-TEST01", reason="t")
    ref = s.escalation.reference
    assert g.check(f"Your reference is {ref}.", s,
                   ReplyBrief(phase="p", goal="g", allowed_facts={"handoff_reference": ref})).ok


def test_ordinal_and_unpadded_dates_are_caught(store):
    g = OutputGuard(store)
    pre = ReplyBrief(phase="VERIFY_ID", goal="g")
    for leak in ("born March 15th, 1985", "born 3/15/1985"):
        assert "identifier:dob" in g.check(f"Thanks, {leak}.", unverified(store), pre).violations, leak
    for leak in ("January 12th, 2026", "1/12/2026"):
        r = g.check(f"That claim was opened on {leak}.", unverified(store), pre)
        assert "fixture_date_before_verification" in r.violations, leak
    s = unverified(store)
    s.verification = Verification(status="verified", party_id="P9", role="policyholder")
    post = ReplyBrief(phase="PROCESS_CASE", goal="g", allowed_facts={"claim_opened": "January 12, 2026"})
    assert g.check("It was opened on January 12th, 2026.", s, post).ok
    for leak in ("February 28th, 2026", "2/28/2026"):
        assert "date_not_allowed" in g.check(f"It was opened on {leak}.", s, post).violations, leak


def test_amounts_match_by_token_and_ignore_thousands_separators(store):
    g = OutputGuard(store)
    pre = ReplyBrief(phase="VERIFY_ID", goal="g")
    for leak in ("$1,450.00", "$3,500.00", "3,500"):
        r = g.check(f"The allowed amount is {leak}.", unverified(store), pre)
        assert "amount_before_verification" in r.violations, leak
    s = unverified(store)
    s.verification = Verification(status="verified", party_id="P9", role="policyholder")
    post = ReplyBrief(phase="PROCESS_CASE", goal="g", allowed_facts={"allowed_max_amount": "$1450.00"})
    assert g.check("The allowed amount is $1450.00.", s, post).ok
    assert "number_not_allowed:450.00" in g.check("Your dental claim allowed $450.00.", s, post).violations
