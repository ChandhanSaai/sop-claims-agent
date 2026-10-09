from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.fake import FakeLLM
from app.main import create_app
from tests.conftest import ROOT


def test_healthz():
    settings = Settings(_env_file=None, fixtures_dir=ROOT / "fixtures", llm_backend="fake")
    app = create_app(settings=settings, llm=FakeLLM())
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_the_app_refuses_to_start_with_the_anthropic_backend_and_no_key():
    import pytest

    from app.config import Settings
    from app.main import create_app

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        create_app(settings=Settings(_env_file=None, llm_backend="anthropic", anthropic_api_key=""))
