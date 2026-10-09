import calendar
import re
from datetime import date

from pydantic import BaseModel, Field

from app.data.normalize import doc_tokens, fmt_date, normalize_name, normalize_phone, parse_dob
from app.data.store import FixtureStore
from app.engine.state import Session
from app.llm.schemas import ReplyBrief

CLAIM_ID = re.compile(r"\bCL-\d+\b", re.IGNORECASE)
TAG = re.compile(r"<[^>]+>")
NUMBER = re.compile(r"\d[\d,]*\.\d+|\d{3,}")  # bare numbers under 100 ("the 2 documents") stay unguarded
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")  # "$1,450.00" -> "$1450.00", not "March 18,2026"
_MONTHS = {name.casefold(): i for names in (calendar.month_name, calendar.month_abbr)
           for i, name in enumerate(names) if name}  # "january" -> 1, "jan" -> 1
_MONTH_WORDS = "|".join(sorted(_MONTHS, key=len, reverse=True))
# any month-name, ISO or m/d/yyyy date mention, fixture or invented:
# "April 30, 2026", "apr 30th", "2026-04-30", "4/30/2026"
GENERIC_DATE = re.compile(rf"\b({_MONTH_WORDS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b"
                          r"|\b(\d{4})-(\d{2})-(\d{2})\b|\b(\d{1,2})/(\d{1,2})/(\d{4})\b", re.IGNORECASE)


def contains_token(haystack: str, needle: str) -> bool:
    """Case-insensitive match on word boundaries,
    so 'March 1' does not match inside 'March 18' or 'March 1985'."""
    return re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", haystack, re.IGNORECASE) is not None


def _suffix(day: int) -> str:
    return "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def date_variants(d: date) -> list[str]:
    o = f"{d.day}{_suffix(d.day)}"  # "15th"
    return [d.isoformat(), fmt_date(d), f"{d:%B} {d.day}", f"{d:%b} {d.day}", f"{d.day} {d:%B} {d.year}",
            f"{d:%m}/{d:%d}/{d.year}", f"{d.month}/{d.day}/{d.year}", f"{d:%B} {o}, {d.year}", f"{d:%B} {o}",
            f"{d:%b} {o}", f"{o} {d:%B} {d.year}", f"{d:%d}/{d:%m}/{d.year}", f"{d.day}/{d.month}/{d.year}",
            f"{d:%d}.{d:%m}.{d.year}",
            f"{d:%B} {d.year}"]  # month-year stays last: pre-verification checks drop it


def _token_run(s: str) -> str:
    """doc_tokens in reading order, space-padded, so a phrase matches only as a contiguous run of tokens."""
    return f" {' '.join(t for w in normalize_name(s).split() for t in doc_tokens(w))} "


class GuardResult(BaseModel):
    ok: bool
    violations: list[str] = Field(default_factory=list)


class OutputGuard:
    """Code-level checks on the Writer's text.
    Pre-verification: nothing from the fixtures that the caller did not say.
    Post-verification: every claim id, date and number must come from allowed_facts."""

    def __init__(self, store: FixtureStore):
        self.amounts = {a for c in store.claims
                        for a in (c.expected_reimbursement_amount, c.allowed_max_amount, c.net_pay, c.net_fee)
                        if a != "0.00"}
        self.dates: set[date] = {c.created_at for c in store.claims} | {
            c.appeal_deadline for c in store.claims if c.appeal_deadline
        }
        self.phrases = {p for c in store.claims
                        for p in (*c.documents_needed, c.denial_reason or "", c.summary) if p}

    def _identifier_leaks(self, text: str, session: Session) -> list[str]:
        low, digits, out = text.lower(), re.sub(r"\D", "", text), []
        m = session.memory
        if dob := m.value("dob"):  # the stored value too: ISO from the Reader, or as written when ambiguous
            d = parse_dob(dob)[0]
            # a partial value ("March") is not an echo to hunt for: the raw check needs two digit runs
            raw = len(re.findall(r"\d+", dob)) >= 2 and contains_token(text, dob)
            if raw or (d and any(contains_token(text, v) for v in date_variants(d))):
                out.append("dob")
        if (ph := m.value("phone")) and (p := normalize_phone(ph)) and p[2:] in digits:
            out.append("phone")
        if (em := m.value("email")) and em.lower() in low:
            out.append("email")
        if (id4 := m.value("id_last4")) and re.search(rf"\b{re.escape(id4)}\b", text):
            out.append("id_last4")
        if (pn := m.value("policy_number")) and pn.lower() in low:
            out.append("policy_number")
        return out

    def check(self, text: str, session: Session, brief: ReplyBrief) -> GuardResult:
        v: list[str] = []
        if TAG.search(text):
            v.append("markup_tag")
        v += [f"identifier:{x}" for x in self._identifier_leaks(text, session)]
        user_text = " ".join(t.text for t in session.transcript if t.role == "user")
        allowed = " ".join(brief.allowed_facts.values())
        # amounts and numbers compare without thousands separators
        plain, user_plain, allowed_plain = (THOUSANDS.sub("", x) for x in (text, user_text, allowed))
        if session.verification.status != "verified":
            if CLAIM_ID.search(text):
                v.append("claim_id_before_verification")
            # whole dollars, so "3500" is caught and still matches "3500.00"
            if any(contains_token(plain, a.removesuffix(".00")) for a in self.amounts):
                v.append("amount_before_verification")
            for d in self.dates:
                # month-year alone is the caller's own words
                if any(contains_token(text, x) for x in date_variants(d)[:-1]):
                    v.append("fixture_date_before_verification")
                    break
            user_run, reply_run = _token_run(user_text), _token_run(text)
            for p in self.phrases:
                pr = _token_run(p)
                if pr.strip() and pr in reply_run and pr not in user_run:
                    v.append(f"phrase_before_verification:{p[:30]}")
                    break
        else:
            for cid in CLAIM_ID.findall(text):
                if cid.upper() not in allowed.upper():
                    v.append(f"claim_id_not_allowed:{cid}")
            allowed_dates = {d for d in self.dates
                             if any(contains_token(allowed, x) for x in date_variants(d))}
            allowed_months = {(d.year, d.month) for d in allowed_dates}
            for d in self.dates - allowed_dates:
                # its month-year alone ("March 2026") also names an allowed date in that month
                xs = date_variants(d)[:-1] if (d.year, d.month) in allowed_months else date_variants(d)
                if any(contains_token(text, x) for x in xs):
                    v.append("date_not_allowed")
                    break
            for m in GENERIC_DATE.finditer(text):  # an invented date is a violation too, same granularity
                if m[1]:
                    month, day, year = _MONTHS[m[1].casefold()], int(m[2]), int(m[3]) if m[3] else None
                elif m[4]:
                    month, day, year = int(m[5]), int(m[6]), int(m[4])
                else:
                    month, day, year = int(m[7]), int(m[8]), int(m[9])
                if not any(d.month == month and d.day == day and year in (None, d.year)
                           for d in allowed_dates):
                    v.append(f"date_not_allowed:{m[0]}")
                    break
            ref = session.escalation.reference or ""
            for n in NUMBER.findall(plain):
                if not (contains_token(allowed_plain, n) or contains_token(user_plain, n) or n in ref):
                    v.append(f"number_not_allowed:{n}")
        return GuardResult(ok=not v, violations=v)
