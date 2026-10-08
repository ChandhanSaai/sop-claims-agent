import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.config import Settings
from app.data.models import Claim, ConsentScenario, EmailRecord, Guideline, Policyholder, Representative
from app.data.normalize import (
    docs_match,
    mask_email,
    normalize_email,
    normalize_id4,
    normalize_name,
    normalize_phone,
    parse_dob,
)
from app.data.store import FixtureStore

IDENTIFIERS = ("full_name", "dob", "phone", "email", "id_last4")
STRONG_FIELDS = ("dob", "id_last4")


class VerificationResult(BaseModel):
    passed: bool
    matched_count: int


class PolicyholderRepo:
    def __init__(self, records: list[Policyholder]):
        self._records = records
        self._by_id = {r.party_id: r for r in records}

    @staticmethod
    def _names(r: Policyholder) -> set[str]:
        return {normalize_name(r.name), *(normalize_name(a) for a in r.name_aliases)}

    @staticmethod
    def _phones(r: Policyholder) -> set[str]:
        return {p for p in (normalize_phone(x) for x in (r.phone, *r.phone_aliases)) if p}

    @staticmethod
    def _emails(r: Policyholder) -> set[str]:
        return {normalize_email(x) for x in (r.email, *r.email_aliases)}

    def get(self, party_id: str) -> Policyholder:
        return self._by_id[party_id]

    def find(self, *, policy_number: str | None = None, phone: str | None = None,
             email: str | None = None, name: str | None = None) -> list[Policyholder]:
        """First unique key present wins: policy number, phone, email, then name (which may collide)."""
        if policy_number:
            key = policy_number.strip().casefold()
            return [r for r in self._records if r.policy_number.casefold() == key]
        if phone and (p := normalize_phone(phone)):
            return [r for r in self._records if p in self._phones(r)]
        if email:
            e = normalize_email(email)
            return [r for r in self._records if e in self._emails(r)]
        if name:
            n = normalize_name(name)
            return [r for r in self._records if n in self._names(r)]
        return []

    def verify(self, record: Policyholder, provided: dict[str, str], *, min_fields: int,
               require_strong: bool) -> VerificationResult:
        """Pass/fail only. Policy number is never counted. Near misses are misses."""
        matched: list[str] = []
        if "full_name" in provided and normalize_name(provided["full_name"]) in self._names(record):
            matched.append("full_name")
        if "dob" in provided:
            d, _ambiguous = parse_dob(provided["dob"])
            if d is not None and d == record.dob:
                matched.append("dob")
        if "phone" in provided and normalize_phone(provided["phone"]) in self._phones(record):
            matched.append("phone")
        if "email" in provided and normalize_email(provided["email"]) in self._emails(record):
            matched.append("email")
        if "id_last4" in provided and normalize_id4(provided["id_last4"]) == record.id_last4:
            matched.append("id_last4")
        passed = len(matched) >= min_fields and (
            not require_strong or any(m in STRONG_FIELDS for m in matched)
        )
        return VerificationResult(passed=passed, matched_count=len(matched))


class ClaimsRepo:
    def __init__(self, claims: list[Claim]):
        self._claims = claims
        self._by_id = {c.case_id: c for c in claims}

    def get(self, case_id: str) -> Claim:
        return self._by_id[case_id]

    def for_party(self, party_id: str) -> list[Claim]:
        return [c for c in self._claims if c.party_id == party_id]

    @staticmethod
    def filter(claims: list[Claim], *, case_type: str | None = None, status: str | None = None,
               month: int | None = None, year: int | None = None, case_id: str | None = None) -> list[Claim]:
        out = claims
        if case_id:
            out = [c for c in out if c.case_id.casefold() == case_id.strip().casefold()]
        if case_type:
            out = [c for c in out if c.case_type == case_type]
        if status:
            out = [c for c in out if c.status == status]
        if month:
            out = [c for c in out if c.created_at.month == month]
        if year:
            out = [c for c in out if c.created_at.year == year]
        return out


class GuidelineRepo:
    def __init__(self, g: Guideline):
        self._g = g

    def topics(self) -> list[str]:
        return [t.topic for t in self._g.claim_followup_guidance]

    def processing_time(self) -> str:
        return self._g.claim_followup_settings["average_processing_time_after_submission"]["en"]

    def human_review(self) -> str:
        return self._g.claim_followup_settings["human_review_after_document_alternatives_exhausted"]["en"]

    def topic_text(self, topic: str, claim: Claim) -> str | None:
        t = next((x for x in self._g.claim_followup_guidance if x.topic == topic), None)
        if t is None or (t.requires_documents and not claim.documents_needed):
            return None
        return t.en.format(
            case_id=claim.case_id,
            documents=", ".join(claim.documents_needed),
            average_processing_time_after_submission=self.processing_time(),
        )

    def _match_key(self, table: dict[str, dict[str, str]], doc: str) -> tuple[str, str] | None:
        for key, val in table.items():
            if key != "default" and docs_match(doc, key):
                return key, val["en"]
        return None

    def document_guidance(self, doc: str) -> tuple[str, str] | None:
        return self._match_key(self._g.document_guidance, doc)

    def document_alternative(self, doc: str) -> tuple[str, str] | None:
        return self._match_key(self._g.document_alternative_guidance, doc)

    def case_type_guidance(self, case_type: str) -> str | None:
        entry = self._g.case_type_guidance.get(case_type)
        return entry["en"] if entry else None

    def default_guidance(self) -> str:
        return self._g.default_guidance["en"]

    def default_alternative(self) -> str:
        return self._g.document_alternative_guidance["default"]["en"]

    def fallback(self) -> str:
        return self._g.claim_followup_fallback["en"]


class RepresentativeRepo:
    def __init__(self, reps: list[Representative]):
        self._reps = reps

    def match(self, rep_name: str, policyholder_name: str) -> Representative | None:
        rn, pn = normalize_name(rep_name), normalize_name(policyholder_name)
        for r in self._reps:
            if normalize_name(r.rep_name) == rn and normalize_name(r.buyer_name) == pn:
                return r
        return None


class ConsentService:
    """Simulated out-of-band consent. One request per session; each poll advances the scenario sequence."""

    def __init__(self, scenarios: dict[str, ConsentScenario]):
        self._scenarios = scenarios
        self._requests: dict[str, dict] = {}

    def request(self, party_id: str, rep_name: str, scenario: str) -> str:
        cid = f"CON-{len(self._requests) + 1:04d}"
        self._requests[cid] = {"seq": list(self._scenarios[scenario].status_sequence), "polls": 0,
                               "party_id": party_id, "rep_name": rep_name}
        return cid

    def poll(self, consent_id: str) -> Literal["pending", "approved", "timed_out"]:
        r = self._requests[consent_id]
        if r["polls"] >= len(r["seq"]):
            return "timed_out"
        status = r["seq"][r["polls"]]
        r["polls"] += 1
        return "approved" if status == "approved" else "pending"


class EmailOutbox:
    def __init__(self, path: Path | None = None):
        self._path = path
        self._records: list[EmailRecord] = []

    def send(self, to_email: str, subject: str, body: str) -> EmailRecord:
        rec = EmailRecord(
            id=f"EML-{len(self._records) + 1:04d}", to=to_email, to_masked=mask_email(to_email),
            subject=subject, body=body, sent_at=datetime.now(UTC).isoformat(),
        )
        self._records.append(rec)
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec.model_dump(exclude={"to"})) + "\n")
        return rec

    def list(self) -> list[EmailRecord]:
        return list(self._records)


@dataclass
class Repos:
    store: FixtureStore
    policyholders: PolicyholderRepo
    claims: ClaimsRepo
    guideline: GuidelineRepo
    representatives: RepresentativeRepo
    consent: ConsentService
    outbox: EmailOutbox


def build_repos(store: FixtureStore, settings: Settings) -> Repos:
    return Repos(
        store=store,
        policyholders=PolicyholderRepo(store.policyholders),
        claims=ClaimsRepo(store.claims),
        guideline=GuidelineRepo(store.guideline),
        representatives=RepresentativeRepo(store.representatives),
        consent=ConsentService(store.consent_scenarios),
        outbox=EmailOutbox(settings.traces_dir / "outbox.jsonl"),
    )
