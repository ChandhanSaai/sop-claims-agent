import json
from dataclasses import dataclass
from pathlib import Path

from app.data.models import Claim, ConsentScenario, Guideline, Policyholder, Representative


@dataclass
class FixtureStore:
    policyholders: list[Policyholder]
    claims: list[Claim]
    representatives: list[Representative]
    guideline: Guideline
    consent_scenarios: dict[str, ConsentScenario]

    @classmethod
    def load(cls, fixtures_dir: Path) -> "FixtureStore":
        def read(name: str):
            with open(fixtures_dir / name, encoding="utf-8") as f:
                return json.load(f)

        return cls(
            policyholders=[Policyholder.model_validate(x) for x in read("policyholders.json")],
            claims=[Claim.model_validate(x) for x in read("claims.json")],
            representatives=[Representative.model_validate(x) for x in read("representatives.json")],
            guideline=Guideline.model_validate(read("required_document_guideline.json")),
            consent_scenarios={
                k: ConsentScenario.model_validate(v) for k, v in read("consent_scenarios.json").items()
            },
        )
