import json
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str = ""
    telegram_admin_ids: list[int] = Field(default_factory=list)
    yandex_disk_token: str = ""
    yandex_disk_root: str = "disk:/Telegram Uploads"
    database_url: str = "postgresql+asyncpg://bot:bot@localhost:5432/bot"
    redis_url: str = "redis://localhost:6379/0"
    app_env: str = "dev"
    log_level: str = "INFO"
    temp_storage_dir: Path = Path("./var/tmp_uploads")
    max_file_size_mb: int = 20
    allow_user_downloads: bool = False
    allow_user_folder_selection: bool = True
    rejected_retention_days: int = 7
    webapp_url: str = ""
    webapp_auth_max_age_seconds: int = 86400
    cors_origins: list[str] = Field(default_factory=list)
    upload_worker_poll_seconds: float = 2
    upload_worker_lease_seconds: int = 600
    upload_worker_heartbeat_seconds: int = 30
    telegram_outbox_poll_seconds: float = 1
    telegram_outbox_lease_seconds: int = 60
    telegram_outbox_max_attempts: int = 10
    telegram_outbox_base_retry_seconds: int = 2
    telegram_outbox_max_retry_seconds: int = 900

    @field_validator("telegram_admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: str | Iterable[int | str]) -> list[int]:
        if isinstance(value, str):
            if not value:
                return []
            if value.strip().startswith("["):
                parsed = json.loads(value)
                return [int(v) for v in parsed]
            return [int(part.strip()) for part in value.split(",") if part.strip()]
        if not value:
            return []
        return [int(v) for v in value]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | Iterable[str]) -> list[str]:
        if isinstance(value, str):
            if not value:
                return []
            if value.strip().startswith("["):
                parsed = json.loads(value)
                return [str(part).strip() for part in parsed if str(part).strip()]
            return [part.strip() for part in value.split(",") if part.strip()]
        if not value:
            return []
        return [str(part).strip() for part in value if str(part).strip()]

    @field_validator(
        "upload_worker_poll_seconds",
        "upload_worker_lease_seconds",
        "upload_worker_heartbeat_seconds",
        "telegram_outbox_poll_seconds",
        "telegram_outbox_lease_seconds",
        "telegram_outbox_max_attempts",
        "telegram_outbox_base_retry_seconds",
        "telegram_outbox_max_retry_seconds",
    )
    @classmethod
    def positive_worker_timing(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("worker timing values must be positive")
        return value

    def model_post_init(self, __context) -> None:  # type: ignore[no-untyped-def]
        if self.upload_worker_heartbeat_seconds >= self.upload_worker_lease_seconds:
            raise ValueError(
                "UPLOAD_WORKER_HEARTBEAT_SECONDS must be less than UPLOAD_WORKER_LEASE_SECONDS"
            )
        if self.upload_worker_poll_seconds < 0.5:
            raise ValueError("UPLOAD_WORKER_POLL_SECONDS must be at least 0.5 to avoid busy loop")
        if self.telegram_outbox_poll_seconds < 0.5:
            raise ValueError("TELEGRAM_OUTBOX_POLL_SECONDS must be at least 0.5 to avoid busy loop")
        if self.telegram_outbox_max_retry_seconds < self.telegram_outbox_base_retry_seconds:
            raise ValueError(
                "TELEGRAM_OUTBOX_MAX_RETRY_SECONDS must be >= TELEGRAM_OUTBOX_BASE_RETRY_SECONDS"
            )

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
