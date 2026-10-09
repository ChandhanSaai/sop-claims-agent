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
_ENGLISH = set(_MONTHS)
# the Writer answers in the caller's language: month words in the languages the demo is likely to meet
for _words in ("enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre",
               "janvier février mars avril mai juin juillet août septembre octobre novembre décembre",
               "januar februar märz april mai juni juli august september oktober november dezember",
               "janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro dezembro",
               "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre "
               "dicembre"):
    _MONTHS.update({w: i for i, w in enumerate(_words.split(), start=1)})
_MONTH_WORDS = "|".join(sorted(_MONTHS, key=len, reverse=True))
# any date mention, fixture or invented, month first or day first, ISO or m/d/yyyy: "April 30, 2026",
# "apr 30th", "30 April 2026", "the 30th of April", "30 de abril de 2026", "30. April 2026", "2026-04-30",
# "4/30/2026". A day-first English month needs a year, an ordinal or "of" ("2 January claims", "1. March" and
# "the 2 may differ" are counts and list numbers), and a month word followed by a day is read month first.
GENERIC_DATE = re.compile(
    rf"\b(?P<m1>{_MONTH_WORDS})\.?\s+(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<y1>\d{{4}}))?\b"
    rf"|\b(?:(?P<prep>am|vom|bis|zum|den|der)\s+)?(?P<d2>\d{{1,2}})(?P<mark>st|nd|rd|th|\.)?"
    rf"(?:\s+(?P<of>of|de))?\s+(?P<m2>{_MONTH_WORDS})"
    rf"(?!\.?\s+\d{{1,2}}(?!\d))\.?(?:,?\s+(?:de\s+)?(?P<y2>\d{{4}}))?\b"
    r"|\b(?P<y3>\d{4})[-/.](?P<m3>\d{2})[-/.](?P<d3>\d{2})\b"
    r"|\b(?P<m4>\d{1,2})(?P<sep>[/.-])(?P<d4>\d{1,2})(?P=sep)(?P<y4>\d{2,4})\b",
    re.IGNORECASE)


def date_mention(m: re.Match) -> tuple[int, int, int | None] | None:
    """(month, day, year or None) for a GENERIC_DATE match; None when the match is not a date after all."""
    g = m.groupdict()
    if g["y3"]:
        return int(g["m3"]), int(g["d3"]), int(g["y3"])
    if g["y4"]:
        mo, da, yr = int(g["m4"]), int(g["d4"]), int(g["y4"])
        if mo > 12 >= da:  # 18/03/2026: the month is impossible, so the day came first
            mo, da = da, mo
        if yr < 100:  # 3/15/85
            yr += 1900 if yr > 30 else 2000
        return mo, da, yr
    mo, da, yr = ("m1", "d1", "y1") if g["m1"] else ("m2", "d2", "y2")
    word = g[mo].casefold().replace("\u0131", "i")  # IGNORECASE matches a dotless i that casefold keeps
    month = _MONTHS.get(word)
    if month is None:
        return None
    dot_only = g["mark"] == "." and not g["prep"]  # "1. March 2026" is a list item, "am 10. September" a day
    marked = ((g[yr] and not dot_only) or g["mark"] in ("st", "nd", "rd", "th") or g["of"]
              or (g["prep"] and g["mark"]))
    if mo == "m2" and word in _ENGLISH and not marked:
        return None  # a count or a list number before an English month word
    return month, int(g[da]), int(g[yr]) if g[yr] else None


def mentions(text: str) -> list[tuple[int, int, int | None]]:
    return [d for m in GENERIC_DATE.finditer(text) if (d := date_mention(m)) is not None]


def names_date(ms: list[tuple[int, int, int | None]], d: date) -> bool:
    """A mention of the day and month of d with its year or none: "18 de marzo" names March 18, 2026."""
    return any(mo == d.month and da == d.day and yr in (None, d.year) for mo, da, yr in ms)


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
            f"{d:%d}.{d:%m}.{d.year}", f"{d.day} {d:%B}", f"{d.day} {d:%b}",
            f"{d:%d}-{d:%m}-{d.year}", f"{d.day}-{d.month}-{d.year}", f"{d:%m}-{d:%d}-{d.year}",
            f"{d.month}-{d.day}-{d.year}", f"{d.day}.{d.month}.{d.year}", f"{d.year}/{d:%m}/{d:%d}",
            f"{d.year}.{d:%m}.{d:%d}", f"{d:%m}/{d:%d}/{d:%y}", f"{d.month}/{d.day}/{d:%y}",
            f"{d:%d}/{d:%m}/{d:%y}", f"{d.day}/{d.month}/{d:%y}", f"{d:%d}.{d:%m}.{d:%y}",
            f"{d:%d}-{d:%m}-{d:%y}", f"{d:%m}-{d:%d}-{d:%y}",
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
            echoed = d and (any(contains_token(text, v) for v in date_variants(d))
                            or names_date(mentions(text), d))
            if raw or echoed:
                out.append("dob")
        if (ph := m.value("phone")) and (p := normalize_phone(ph)) and p[2:] in digits:
            out.append("phone")
        if (em := m.value("email")) and em.lower() in low:
            out.append("email")
        if (id4 := m.value("id_last4")) and re.search(rf"\b{re.escape(id4)}\b", text):
            out.append("id_last4")
        if pn := m.value("policy_number"):
            digits_pn = re.sub(r"\D", "", pn)
            if pn.lower() in low or (len(digits_pn) >= 4 and contains_token(text, digits_pn)):
                out.append("policy_number")
        return out

    def check(self, text: str, session: Session, brief: ReplyBrief) -> GuardResult:
        v: list[str] = []
        if TAG.search(text):
            v.append("markup_tag")
        v += [f"identifier:{x}" for x in self._identifier_leaks(text, session)]
        window = session.transcript[session.transcript_fence:]  # the earlier party's words are not exemptions
        user_text = " ".join(t.text for t in window if t.role == "user")
        allowed = " ".join(brief.allowed_facts.values())
        # amounts and numbers compare without thousands separators
        plain, user_plain, allowed_plain = (THOUSANDS.sub("", x) for x in (text, user_text, allowed))
        if session.verification.status != "verified":
            if CLAIM_ID.search(text):
                v.append("claim_id_before_verification")
            # whole dollars, so "3500" is caught and still matches "3500.00"; an amount or date the caller
            # typed is theirs to hear back, like a document phrase, so the verdict never tells a guess from a
            # fixture value
            amounts = (a.removesuffix(".00") for a in self.amounts)
            if any(contains_token(plain, a) and not contains_token(user_plain, a) for a in amounts):
                v.append("amount_before_verification")
            ms, said_ms = mentions(text), mentions(user_text)
            for d in self.dates:
                forms = date_variants(d)[:-1]  # month-year alone is the caller's own words
                said = any(contains_token(user_text, x) for x in forms) or names_date(said_ms, d)
                if not said and (any(contains_token(text, x) for x in forms) or names_date(ms, d)):
                    v.append("fixture_date_before_verification")
                    break
            user_run, reply_run = _token_run(user_text), _token_run(text)
            for p in self.phrases:
                pr = _token_run(p)
                if pr.strip() and pr in reply_run and pr not in user_run:
                    v.append("phrase_before_verification")
                    break
        else:
            for cid in CLAIM_ID.findall(text):
                if cid.upper() not in allowed.upper():
                    v.append("claim_id_not_allowed")
            allowed_dates = {d for d in self.dates
                             if any(contains_token(allowed, x) for x in date_variants(d))}
            allowed_months = {(d.year, d.month) for d in allowed_dates}
            for d in self.dates - allowed_dates:
                # its month-year alone ("March 2026") also names an allowed date in that month
                xs = date_variants(d)[:-1] if (d.year, d.month) in allowed_months else date_variants(d)
                if any(contains_token(text, x) for x in xs):
                    v.append("date_not_allowed")
                    break
            # a date written into allowed_facts (today's date, a deadline) is allowed at the same granularity
            allowed_mentions = set(mentions(allowed))
            for month, day, year in mentions(text):  # an invented date is a violation too, same granularity
                known = any(d.month == month and d.day == day and year in (None, d.year)
                            for d in allowed_dates)
                mentioned = any(am == month and ad == day and year in (None, ay)
                                for am, ad, ay in allowed_mentions)
                if not (known or mentioned):
                    v.append("date_not_allowed")
                    break
            ref = session.escalation.reference or ""
            for n in NUMBER.findall(plain):
                if not (contains_token(allowed_plain, n) or contains_token(user_plain, n) or n in ref):
                    v.append("number_not_allowed")
        # kinds only, once each: the matched values would otherwise reach the trace and the inspector
        return GuardResult(ok=not v, violations=list(dict.fromkeys(v)))
