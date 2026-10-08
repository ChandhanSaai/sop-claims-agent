import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.engine.state import Session, Turn
from app.main import create_app


@dataclass
class StubResult:
    reply: str
    trace: dict


class StubService:
    def start(self, scenario: str) -> Session:
        s = Session.new(scenario)
        s.transcript.append(Turn(role="assistant", text="Hello, I'm an automated assistant."))
        return s

    def chat(self, session: Session, message: str) -> StubResult:
        session.turn += 1
        session.transcript.append(Turn(role="user", text=message))
        session.transcript.append(Turn(role="assistant", text=f"echo: {message}"))
        return StubResult(reply=f"echo: {message}", trace={"turn": session.turn})

    def outbox(self, session: Session) -> list[dict]:
        return [{"id": "EML-0001", "to_masked": "m*******@email.com"}]


@pytest.fixture
def client():
    app = create_app(settings=Settings(_env_file=None), service=StubService())
    return TestClient(app)


def test_session_then_chat_then_outbox(client):
    r = client.post("/api/session", json={"scenario": "default"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert "automated assistant" in r.json()["greeting"]
    assert r.json()["state"]["phase"] == "VERIFY_ID"
    c = client.post("/api/chat", json={"session_id": sid, "message": "hi"})
    assert c.status_code == 200 and c.json()["reply"] == "echo: hi"
    assert c.json()["state"]["turn"] == 1 and c.json()["trace"] == {"turn": 1}
    o = client.get(f"/api/session/{sid}/outbox")
    assert o.json()["emails"][0]["to_masked"] == "m*******@email.com"
    t = client.get(f"/api/session/{sid}/trace")
    assert t.status_code == 200 and isinstance(t.json()["turns"], list)


def test_unknown_session_is_404_and_empty_message_is_422(client):
    assert client.post("/api/chat", json={"session_id": "nope", "message": "hi"}).status_code == 404
    r = client.post("/api/session", json={}).json()
    assert client.post("/api/chat", json={"session_id": r["session_id"], "message": ""}).status_code == 422


def test_access_token_gate():
    app = create_app(settings=Settings(_env_file=None, demo_access_token="s3cret"), service=StubService())
    c = TestClient(app)
    assert c.post("/api/session", json={}).status_code == 401
    assert c.post("/api/session", json={}, headers={"X-Access-Token": "wrong"}).status_code == 401
    non_ascii = {"X-Access-Token": "s3cr\xe9t".encode("latin-1")}  # 401, not a compare_digest TypeError
    assert c.post("/api/session", json={}, headers=non_ascii).status_code == 401
    assert c.post("/api/session", json={}, headers={"X-Access-Token": "s3cret"}).status_code == 200
    assert c.get("/healthz").status_code == 200
    assert c.get("/ui/app.js").status_code == 200


def test_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "SOP inspector" in r.text


def test_expired_session_is_404(client, monkeypatch):
    sid = client.post("/api/session", json={}).json()["session_id"]
    later = time.monotonic() + client.app.state.settings.session_ttl_minutes * 60 + 1
    # patch the store's clock only: a global time.monotonic patch would also freeze the event loop
    monkeypatch.setattr("app.api.sessions.time", SimpleNamespace(monotonic=lambda: later))
    assert client.post("/api/chat", json={"session_id": sid, "message": "hi"}).status_code == 404


class SlowService(StubService):
    def chat(self, session: Session, message: str) -> StubResult:
        read = session.turn
        time.sleep(0.05)  # widen the read-modify-write window
        session.turn = read + 1
        return StubResult(reply="ok", trace={})


def test_concurrent_chats_on_one_session_are_serialised():
    c = TestClient(create_app(settings=Settings(_env_file=None), service=SlowService()))
    sid = c.post("/api/session", json={}).json()["session_id"]
    body = {"session_id": sid, "message": "hi"}
    with ThreadPoolExecutor(max_workers=5) as pool:
        rs = list(pool.map(lambda _: c.post("/api/chat", json=body), range(5)))
    assert sorted(r.json()["state"]["turn"] for r in rs) == [1, 2, 3, 4, 5]
    assert c.app.state.sessions.get(sid).turn == 5
