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
                 "born 1985 March 15", "born 15.03.1985", "born 15/03/1985", "born 15/3/1985",
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
    # "345" is a three-digit run that only the reference allows
    s.escalation = Escalation(requested=True, reference="ESC-12A345", reason="t")
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
    assert g.check("The allowed amount is $1,450.00.", s, post).ok
    said = unverified(store, user_said="they told me it was $3,500")
    said.verification = Verification(status="verified", party_id="P9", role="policyholder")
    assert g.check("You mentioned $3500.", said, post).ok
    dated = ReplyBrief(phase="PROCESS_CASE", goal="g", allowed_facts={"appeal_deadline": "March 18, 2026"})
    assert g.check("The deadline was March 18,2026.", s, dated).ok  # not a thousands separator
    assert "number_not_allowed" in g.check("Your dental claim allowed $450.00.", s, post).violations


def test_raw_dob_echo_is_caught_in_any_format(store):
    g = OutputGuard(store)
    for raw in ("March 15th, 1985", "15/03/1985"):  # the DD/MM form parses but is not a date_variant
        for verified in (False, True):
            s = unverified(store)
            s.memory.set("dob", raw, 1)
            if verified:
                s.verification = Verification(status="verified", party_id="P9", role="policyholder")
            r = g.check(f"Thanks, born {raw}.", s, ReplyBrief(phase="p", goal="g"))
            assert "identifier:dob" in r.violations, (raw, verified)


def test_partial_raw_dob_does_not_flag_every_month_mention(store):
    g = OutputGuard(store)
    s = unverified(store)
    s.verification = Verification(status="verified", party_id="P9", role="policyholder")
    post = ReplyBrief(phase="PROCESS_CASE", goal="g", allowed_facts={"appeal_deadline": "March 18, 2026"})
    s.memory.set("dob", "March", 1)  # a partial value is not an echo to hunt for
    assert g.check("The appeal deadline was March 18, 2026.", s, post).ok
    s.memory.set("dob", "March 15th, 1985", 1)
    assert "identifier:dob" in g.check("Thanks, born March 15th, 1985.", s, post).violations


def test_month_year_shared_with_an_allowed_date_is_allowed(store):
    g = OutputGuard(store)
    s = unverified(store)
    s.verification = Verification(status="verified", party_id="P9", role="policyholder")
    post = ReplyBrief(phase="PROCESS_CASE", goal="g", allowed_facts={"appeal_deadline": "March 18, 2026"})
    # 2026-03-01 is another fixture date, but "March 2026" also names the allowed one
    assert g.check("The appeal deadline in March 2026 was March 18, 2026.", s, post).ok
    for leak in ("March 1, 2026", "February 2026"):
        assert "date_not_allowed" in g.check(f"It was opened on {leak}.", s, post).violations, leak


def test_invented_dates_are_caught_after_verification(store):
    g = OutputGuard(store)
    s = unverified(store)
    s.verification = Verification(status="verified", party_id="P9", role="policyholder")
    post = ReplyBrief(phase="PROCESS_CASE", goal="g", allowed_facts={"appeal_deadline": "March 18, 2026"})
    for leak in ("You can still appeal until April 30, 2026.", "The deadline was 2026-04-30.",
                 "You can still appeal until April 30th.", "Appeal by Apr 30, 2026.", "Appeal by 4/30/2026.",
                 "appeal by april 30, 2026."):  # none of these is a fixture date
        assert "date_not_allowed" in g.check(leak, s, post).violations, leak
    assert g.check("Please send the 2 documents before March 18; the review takes under a week.", s, post).ok
    for ok in ("The deadline was Mar 18, 2026.", "The deadline was 3/18/2026.", "The deadline was MARCH 18."):
        assert g.check(ok, s, post).ok, ok


def test_phrases_match_only_as_contiguous_token_runs(store):
    g = OutputGuard(store)
    pre = ReplyBrief(phase="VERIFY_ID", goal="g")
    assert g.check("Please note that our office needs to verify you first.", unverified(store), pre).ok
    r = g.check("The review file did not include the pathology report and office note.",
                unverified(store), pre)
    assert "phrase_before_verification" in r.violations
    said = unverified(store, user_said="they said the office note was missing")
    assert g.check("I've noted the office note issue; first I need to verify you.", said, pre).ok
    scattered = unverified(store, user_said="my office sent a note")  # not the phrase, so not an echo
    assert not g.check("I've noted the office note issue.", scattered, pre).ok


def test_a_date_in_allowed_facts_is_allowed_after_verification(store):
    g = OutputGuard(store)
    s = unverified(store)
    s.verification = Verification(status="verified", party_id="P9", role="policyholder")
    post = ReplyBrief(phase="PROCESS_CASE", goal="g",
                      allowed_facts={"today": "October 7, 2026", "appeal_deadline": "March 18, 2026"})
    assert g.check("As of today, October 7, 2026, the March 18, 2026 deadline has passed.", s, post).ok
    assert g.check("As of October 7 the deadline has passed.", s, post).ok  # same date, month-day form
    assert "date_not_allowed" in g.check("As of October 8, 2026 the deadline has passed.", s, post).violations


def test_violations_name_the_kind_only_and_are_deduplicated(store):
    g = OutputGuard(store)
    s = unverified(store)
    s.verification = Verification(status="verified", party_id="P9", role="policyholder")
    post = ReplyBrief(phase="PROCESS_CASE", goal="g", allowed_facts={"claim_id": "CL-2048"})
    r = g.check("Thanks, born March 15, 1985. The allowed amounts were 3500.00 and 450.00; your claims "
                "CL-2011 and CL-1899 were closed on February 28, 2026 and April 30, 2026.", s, post)
    assert set(r.violations) == {"identifier:dob", "claim_id_not_allowed", "date_not_allowed",
                                 "number_not_allowed"}
    assert len(r.violations) == len(set(r.violations))
    for leaked in ("1985", "3500.00", "CL-2011", "February 28, 2026", "April 30, 2026"):
        assert not any(leaked in v for v in r.violations)  # the echoed values stay out of the record
