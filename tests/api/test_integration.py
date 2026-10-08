from datetime import date

from fastapi.testclient import TestClient

from app.config import Settings
from app.engine.service import build_service
from app.llm.fake import FakeLLM
from app.llm.schemas import TurnAnalysis
from app.main import create_app
from tests.conftest import ROOT


def test_margaret_over_http(tmp_path):
    settings = Settings(_env_file=None, fixtures_dir=ROOT / "fixtures", traces_dir=tmp_path / "t",
                        llm_backend="fake")
    llm = FakeLLM([TurnAnalysis.model_validate({
        "identity": {"full_name": "Margaret Chen", "policy_number": "POL-9921", "dob": "1985-03-15",
                     "id_last4": "4472"},
        "caller_role": "policyholder",
        "case_hints": {"case_type": "healthcare", "status": "denied", "month": 1},
        "intent": "denial_question",
    })])
    app = create_app(settings=settings,
                     service=build_service(settings, llm=llm, today=lambda: date(2026, 10, 7)))
    c = TestClient(app)
    sid = c.post("/api/session", json={}).json()["session_id"]
    r = c.post("/api/chat", json={"session_id": sid,
                                  "message": "I'm the policyholder. Margaret Chen, POL-9921 ..."}).json()
    assert r["state"]["phase"] == "PROCESS_CASE" and r["state"]["verification"]["status"] == "verified"
    assert "CL-2048" in r["reply"] and "4472" not in r["reply"]
    assert (r["state"]["memory"]["dob"]["value"] == "******"
            and r["state"]["memory"]["dob"]["status"] == "verified")
    assert r["trace"]["phase_before"] == "VERIFY_ID" and r["trace"]["analysis"]["identity"]["dob"] == "******"
    assert c.get(f"/api/session/{sid}/trace").json()["turns"][0]["turn"] == 1
    assert (tmp_path / "t" / f"{sid}.jsonl").exists()


def test_session_scenario_defaults_to_the_configured_one_and_the_body_wins(tmp_path):
    settings = Settings(_env_file=None, fixtures_dir=ROOT / "fixtures", traces_dir=tmp_path / "t",
                        llm_backend="fake", consent_scenario="timeout")
    c = TestClient(create_app(settings=settings, service=build_service(settings, llm=FakeLLM())))
    assert c.post("/api/session", json={}).json()["state"]["scenario"] == "timeout"
    assert c.post("/api/session", json={"scenario": "default"}).json()["state"]["scenario"] == "default"
    assert c.post("/api/session", json={"scenario": "bogus"}).json()["state"]["scenario"] == "default"
