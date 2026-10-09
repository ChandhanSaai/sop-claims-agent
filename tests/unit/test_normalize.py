from datetime import date

from app.data.normalize import (
    doc_tokens,
    docs_match,
    fmt_date,
    mask_email,
    normalize_email,
    normalize_id4,
    normalize_name,
    normalize_phone,
    parse_dob,
    parse_ordinal,
)


def test_normalize_name():
    assert normalize_name("  Margaret   CHEN ") == "margaret chen"
    assert normalize_name("Ya-Wen Li") == "ya wen li"
    assert normalize_name("José García") == "jose garcia"


def test_normalize_phone_variants():
    assert normalize_phone("650-521-2836") == "+16505212836"
    assert normalize_phone("(650) 521 2836") == "+16505212836"
    assert normalize_phone("+1 650 521 2836") == "+16505212836"
    assert normalize_phone("16505212836") == "+16505212836"
    assert normalize_phone("521-2836") is None


def test_normalize_email_and_id4():
    assert normalize_email("  Margaret@Email.com ") == "margaret@email.com"
    assert normalize_id4("4472") == "4472"
    assert normalize_id4("ending in 4472") == "4472"
    assert normalize_id4("12") is None
    assert normalize_id4("123-45-4472") == "4472"
    assert normalize_id4("44729999") is None
    assert normalize_id4("born 1985, ends 4472") == "4472"


def test_parse_dob_formats():
    assert parse_dob("1985-03-15") == (date(1985, 3, 15), False)
    assert parse_dob("March 15, 1985") == (date(1985, 3, 15), False)
    assert parse_dob("15 March 1985") == (date(1985, 3, 15), False)
    assert parse_dob("03/15/1985") == (date(1985, 3, 15), False)   # day > 12 disambiguates
    assert parse_dob("15/03/1985") == (date(1985, 3, 15), False)
    assert parse_dob("03/05/1985") == (date(1985, 3, 5), True)      # ambiguous: US guess, flagged
    assert parse_dob("March 15th, 1985") == (date(1985, 3, 15), False)
    assert parse_dob("15th March 1985") == (date(1985, 3, 15), False)
    assert parse_dob("1985 march 15th") == (date(1985, 3, 15), False)   # year first, lower case, ordinal
    assert parse_dob("15th of March 1985") == (date(1985, 3, 15), False)
    assert parse_dob("1985 15 March") == (date(1985, 3, 15), False)
    assert parse_dob("March 15, 85") == (None, False)                  # two-digit year: re-ask
    assert parse_dob("sometime in spring 85") == (None, False)
    assert parse_dob("yesterday") == (None, False)


def test_mask_email_and_fmt_date():
    assert mask_email("margaret@email.com") == "m*******@email.com"
    assert mask_email("ava.lopez@email.com") == "a********@email.com"
    assert fmt_date(date(2026, 3, 18)) == "March 18, 2026"
    assert fmt_date(date(2026, 1, 5)) == "January 5, 2026"


def test_doc_matching_is_fuzzy_in_code():
    assert doc_tokens("original pathology report") == {"pathology", "report"}
    assert docs_match("pathology report", "original pathology report")
    assert docs_match("office note", "treating provider office note")
    assert not docs_match("repair estimate", "supplemental accident scene photos")
    assert docs_match("accident photos", "supplemental accident scene photos")


def test_parse_ordinal():
    assert parse_ordinal("the first one") == 1
    assert parse_ordinal("2") == 2
    assert parse_ordinal("second") == 2
    assert parse_ordinal("the dental one") is None


def test_names_in_any_script_keep_their_letters():
    from app.data.normalize import normalize_name

    assert normalize_name("马天") == "马天"
    assert normalize_name("Маргарита Чен") == "маргарита чен"
    assert normalize_name("Margaret, Chen!") == "margaret chen"
