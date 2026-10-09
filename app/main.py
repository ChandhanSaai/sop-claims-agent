from pathlib import Path

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.observability.logging import configure_logging

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


def create_app(settings: Settings | None = None, llm=None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="SOP Claims Agent", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.llm = llm

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
