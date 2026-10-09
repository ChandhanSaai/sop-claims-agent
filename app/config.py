from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    llm_backend: str = "anthropic"  # "anthropic" | "fake"
    reader_model: str = "claude-sonnet-5-5"
    writer_model: str = "claude-sonnet-5-5"
    verify_min_fields: int = 3
    verify_require_strong_field: bool = False
    verify_max_attempts: int = 3
    offtopic_human_offer_at: int = 2
    consent_scenario: str = "default"
    session_ttl_minutes: int = 60
    demo_access_token: str = ""
    require_access_token: bool = False  # refuse to start without demo_access_token (set by fly.toml)
    log_level: str = "INFO"
    port: int = 8000
    fixtures_dir: Path = Path("fixtures")
    traces_dir: Path = Path("traces")


@lru_cache
def get_settings() -> Settings:
    return Settings()
