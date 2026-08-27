from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "ChanAnalyzer"
    app_version: str = "2.0.0"
    environment: Literal["local", "server", "test"] = "local"
    auth_mode: Literal["local", "admin"] = "local"
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'data' / 'chan_v2.db').as_posix()}"
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )
    admin_username: str | None = None
    backup_dir: Path = PROJECT_ROOT / "data" / "backups"
    backup_download_enabled: bool = False
    admin_password: str | None = None
    app_secret_key: str | None = None
    tushare_token: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    siliconflow_api_key: SecretStr | None = None
    ai_provider_order: list[str] = Field(default_factory=lambda: ["deepseek", "siliconflow"])
    deepseek_model: str | None = "deepseek-chat"
    siliconflow_model: str | None = None
    session_cookie_name: str = "chanalyzer_session"
    session_ttl_hours: int = 24
    session_touch_interval_seconds: int = Field(default=300, ge=60, le=3600)
    login_window_seconds: int = Field(default=300, ge=60, le=3600)
    login_max_attempts: int = Field(default=10, ge=1, le=100)
    trusted_proxies: list[str] = Field(default_factory=list)
    cookie_secure: bool = False
    worker_poll_seconds: float = 1.0
    worker_max_attempts: int = 3
    radar_min_current_coverage: float = Field(default=0.99, ge=0.5, le=1.0)
    radar_min_ma20_coverage: float = Field(default=0.95, ge=0.5, le=1.0)
    api_host: str = "127.0.0.1"
    api_port: int = 8011
    log_level: str = "INFO"

    @field_validator("allowed_origins", "trusted_proxies", "ai_provider_order", mode="before")
    @classmethod
    def parse_lists(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def validate_deployment_safety(self) -> "Settings":
        if "*" in self.allowed_origins:
            raise ValueError("ALLOWED_ORIGINS must not contain '*' when credentials are enabled")
        if self.environment == "local" and self.api_host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("local mode may only bind API_HOST to a loopback address")
        if self.environment == "server":
            if self.auth_mode != "admin":
                raise ValueError("ENVIRONMENT=server requires AUTH_MODE=admin")
            if not self.cookie_secure:
                raise ValueError("ENVIRONMENT=server requires COOKIE_SECURE=true")
            if not self.app_secret_key or len(self.app_secret_key) < 48:
                raise ValueError("ENVIRONMENT=server requires a stable APP_SECRET_KEY of at least 48 characters")
            if not self.admin_username or not self.admin_password:
                raise ValueError("ENVIRONMENT=server requires ADMIN_USERNAME and ADMIN_PASSWORD")
            if not self.allowed_origins or any(not origin.startswith("https://") for origin in self.allowed_origins):
                raise ValueError("ENVIRONMENT=server requires explicit HTTPS ALLOWED_ORIGINS")
        allowed_providers = {"deepseek", "siliconflow"}
        unknown = set(self.ai_provider_order) - allowed_providers
        if unknown:
            raise ValueError(f"unsupported AI providers: {', '.join(sorted(unknown))}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
