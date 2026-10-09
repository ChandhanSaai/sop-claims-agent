from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.api.sessions import SessionStore
from app.config import Settings, get_settings
from app.observability.logging import configure_logging

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


def create_app(settings: Settings | None = None, llm=None, service=None) -> FastAPI:
    settings = settings or get_settings()
    if settings.require_access_token and not settings.demo_access_token:
        raise RuntimeError("REQUIRE_ACCESS_TOKEN is set but DEMO_ACCESS_TOKEN is empty: the API would be "
                           "public. Set the token (fly secrets set DEMO_ACCESS_TOKEN=...) or unset "
                           "REQUIRE_ACCESS_TOKEN.")
    configure_logging(settings.log_level)
    app = FastAPI(title="SOP Claims Agent", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.sessions = SessionStore(settings.session_ttl_minutes)
    if service is None:
        from app.engine.service import build_service

        service = build_service(settings, llm=llm)
    app.state.service = service

    @app.middleware("http")
    async def revalidate_ui(request: Request, call_next):
        # the UI files carry no version in their names: browsers must revalidate them (ETag) on every load, or
        # a deploy leaves an old script behind a new page
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/ui/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")

    app.include_router(router)
    app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")
    return app
