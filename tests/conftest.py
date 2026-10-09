from pathlib import Path

import pytest

from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None, fixtures_dir=ROOT / "fixtures", traces_dir=tmp_path / "traces", llm_backend="fake"
    )


@pytest.fixture
def store(settings):
    from app.data.store import FixtureStore

    return FixtureStore.load(settings.fixtures_dir)


@pytest.fixture
def repos(store, settings):
    from app.data.repos import build_repos

    return build_repos(store, settings)
