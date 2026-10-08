from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_healthz():
    app = create_app(settings=Settings(_env_file=None), service=object())
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
