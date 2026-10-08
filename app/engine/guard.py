import re
from datetime import date

from pydantic import BaseModel, Field

from app.data.normalize import doc_tokens, fmt_date, normalize_phone, parse_dob
from app.data.store import FixtureStore
from app.engine.state import Session
from app.llm.schemas import ReplyBrief

CLAIM_ID = re.compile(r"\bCL-\d+\b", re.IGNORECASE)
TAG = re.compile(r"<[^>]+>")
NUMBER = re.compile(r"\d[\d,]*\.\d+|\d{3,}")


def contains_token(haystack: str, needle: str) -> bool:
    """Case-insensitive match on word boundaries,
    so 'March 1' does not match inside 'March 18' or 'March 1985'."""
    return re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", haystack, re.IGNORECASE) is not None


def date_variants(d: date) -> list[str]:
    return [d.isoformat(), fmt_date(d), f"{d:%B} {d.day}", f"{d:%b} {d.day}", f"{d.day} {d:%B} {d.year}",
            f"{d:%m}/{d:%d}/{d.year}", f"{d:%B} {d.year}"]


class GuardResult(BaseModel):
    ok: bool
    violations: list[str] = Field(default_factory=list)


class OutputGuard:
    """Code-level checks on the Writer's text.
    Pre-verification: nothing from the fixtures that the caller did not say.
    Post-verification: every claim id, date and number must come from allowed_facts."""

    def __init__(self, store: FixtureStore):
        self.claim_ids = {c.case_id for c in store.claims}
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
        if (dob := m.value("dob")) and (d := parse_dob(dob)[0]) and any(
            contains_token(text, v) for v in date_variants(d)
        ):
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
        if session.verification.status != "verified":
            if CLAIM_ID.search(text):
                v.append("claim_id_before_verification")
            if any(a in text for a in self.amounts):
                v.append("amount_before_verification")
            for d in self.dates:
                # month-year alone is the caller's own words
                if any(contains_token(text, x) for x in date_variants(d)[:-1]):
                    v.append("fixture_date_before_verification")
                    break
            user_tokens = doc_tokens(user_text)
            reply_tokens = doc_tokens(text)
            for p in self.phrases:
                pt = doc_tokens(p)
                if pt and pt <= reply_tokens and not pt <= user_tokens:
                    v.append(f"phrase_before_verification:{p[:30]}")
                    break
        else:
            allowed = " ".join(brief.allowed_facts.values())
            for cid in CLAIM_ID.findall(text):
                if cid.upper() not in allowed.upper():
                    v.append(f"claim_id_not_allowed:{cid}")
            allowed_dates = {d for d in self.dates
                             if any(contains_token(allowed, x) for x in date_variants(d))}
            for d in self.dates:
                if d not in allowed_dates and any(contains_token(text, x) for x in date_variants(d)):
                    v.append("date_not_allowed")
                    break
            ref = session.escalation.reference or ""
            for n in NUMBER.findall(text):
                if n not in allowed and n not in user_text and n not in ref:
                    v.append(f"number_not_allowed:{n}")
        return GuardResult(ok=not v, violations=v)
