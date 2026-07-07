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

    @field_validator("telegram_admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: str | list[int]) -> list[int]:
        if isinstance(value, list):
            return [int(v) for v in value]
        if not value:
            return []
        return [int(part.strip()) for part in value.split(",") if part.strip()]

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
