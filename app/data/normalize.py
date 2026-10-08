import re
from datetime import date, datetime

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")
_STOP = {"the", "a", "an", "of", "and", "or", "to", "for", "in", "on", "at", "by", "with", "original"}
# not "one": "the denied one" is not a pick
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
_DOB_FORMATS = (
    "%Y-%m-%d", "%B %d, %Y", "%B %d %Y", "%d %B %Y", "%b %d, %Y", "%b %d %Y", "%d %b %Y", "%Y/%m/%d"
)


def normalize_name(s: str) -> str:
    return _WS.sub(" ", _NON_ALNUM.sub(" ", s.casefold())).strip()


def normalize_phone(s: str) -> str | None:
    digits = re.sub(r"\D", "", s)
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def normalize_email(s: str) -> str:
    return s.strip().casefold()


def normalize_id4(s: str) -> str | None:
    m = re.search(r"\d{4}", s)
    return m.group(0) if m else None


def parse_dob(s: str) -> tuple[date | None, bool]:
    """Return (date, ambiguous). Ambiguous means a numeric date where day and month could be swapped."""
    s = s.strip()
    for fmt in _DOB_FORMATS:
        try:
            return datetime.strptime(s, fmt).date(), False
        except ValueError:
            continue
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if not m:
        return None, False
    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        if a > 12 and b <= 12:
            return date(y, b, a), False  # DD/MM/YYYY
        if b > 12 and a <= 12:
            return date(y, a, b), False  # MM/DD/YYYY
        if a == b:
            return date(y, a, b), False
        return date(y, a, b), True  # both could be the month: US guess, flagged
    except ValueError:
        return None, False


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return local[:1] + "*" * (len(local) - 1) + "@" + domain


def fmt_date(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def doc_tokens(s: str) -> set[str]:
    out = set()
    for t in normalize_name(s).split():
        if t in _STOP:
            continue
        if t.endswith("s") and len(t) > 3:
            t = t[:-1]
        out.add(t)
    return out


def docs_match(a: str, b: str) -> bool:
    ta, tb = doc_tokens(a), doc_tokens(b)
    return bool(ta) and bool(tb) and (ta <= tb or tb <= ta)


def parse_ordinal(text: str) -> int | None:
    words = normalize_name(text).split()
    for w in words:
        if w.isdigit():
            return int(w)
        if w in _ORDINALS:
            return _ORDINALS[w]
    return None
