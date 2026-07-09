from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import AppSetting
from app.services.disk_paths import validate_yandex_disk_root

SETTING_YANDEX_DISK_ROOT = "yandex_disk_root"


@dataclass(frozen=True)
class RuntimeSetting:
    value: str
    is_default: bool


async def get_yandex_disk_root_setting(session: AsyncSession, settings: Settings) -> RuntimeSetting:
    row = await session.scalar(select(AppSetting).where(AppSetting.key == SETTING_YANDEX_DISK_ROOT))
    if row is None:
        return RuntimeSetting(validate_yandex_disk_root(settings.yandex_disk_root), True)
    return RuntimeSetting(validate_yandex_disk_root(row.value), False)


async def get_yandex_disk_root(session: AsyncSession, settings: Settings) -> str:
    return (await get_yandex_disk_root_setting(session, settings)).value


async def set_yandex_disk_root(session: AsyncSession, root: str, actor_telegram_id: int) -> str:
    normalized = validate_yandex_disk_root(root)
    row = await session.scalar(select(AppSetting).where(AppSetting.key == SETTING_YANDEX_DISK_ROOT))
    if row is None:
        row = AppSetting(
            key=SETTING_YANDEX_DISK_ROOT,
            value=normalized,
            updated_by=actor_telegram_id,
        )
        session.add(row)
    else:
        row.value = normalized
        row.updated_by = actor_telegram_id
    await session.flush()
    return normalized
