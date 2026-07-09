from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import AppSetting
from app.services.disk_paths import validate_yandex_disk_root

YANDEX_DISK_ROOT_KEY = "yandex_disk_root"


async def get_yandex_disk_root_setting(session: AsyncSession) -> AppSetting | None:
    return await session.scalar(select(AppSetting).where(AppSetting.key == YANDEX_DISK_ROOT_KEY))


async def get_yandex_disk_root(session: AsyncSession, settings: Settings) -> str:
    setting = await get_yandex_disk_root_setting(session)
    if setting and setting.value:
        return validate_yandex_disk_root(setting.value)
    return validate_yandex_disk_root(settings.yandex_disk_root)


async def set_yandex_disk_root(
    session: AsyncSession,
    root: str,
    updated_by: int | None = None,
) -> AppSetting:
    value = validate_yandex_disk_root(root)
    setting = await get_yandex_disk_root_setting(session)
    if setting is None:
        setting = AppSetting(key=YANDEX_DISK_ROOT_KEY, value=value, updated_by=updated_by)
        session.add(setting)
    else:
        setting.value = value
        setting.updated_by = updated_by

    await session.flush()
    return setting
