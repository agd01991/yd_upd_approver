from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import AppSetting
from app.services.disk_paths import validate_yandex_disk_root

YANDEX_DISK_ROOT_KEY = "yandex_disk_root"


@dataclass(frozen=True)
class YandexDiskRootSetting:
    value: str
    is_default: bool


async def get_yandex_disk_root_setting(
    session: AsyncSession, settings: Settings
) -> YandexDiskRootSetting:
    row = await session.scalar(select(AppSetting).where(AppSetting.key == YANDEX_DISK_ROOT_KEY))
    if row:
        return YandexDiskRootSetting(validate_yandex_disk_root(row.value), is_default=False)
    return YandexDiskRootSetting(
        validate_yandex_disk_root(settings.yandex_disk_root), is_default=True
    )


async def get_yandex_disk_root(session: AsyncSession, settings: Settings) -> str:
    current = await get_yandex_disk_root_setting(session, settings)
    return current.value


async def set_yandex_disk_root(
    session: AsyncSession,
    root: str,
    actor_telegram_id: int,
) -> str:
    normalized = validate_yandex_disk_root(root)
    row = await session.scalar(select(AppSetting).where(AppSetting.key == YANDEX_DISK_ROOT_KEY))
    if row:
        row.value = normalized
        row.updated_by = actor_telegram_id
    else:
        row = AppSetting(
            key=YANDEX_DISK_ROOT_KEY,
            value=normalized,
            updated_by=actor_telegram_id,
        )
        session.add(row)
    await session.flush()
    return normalized
