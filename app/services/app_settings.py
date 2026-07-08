from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services.disk_paths import validate_yandex_disk_root


async def get_yandex_disk_root(session: AsyncSession, settings: Settings) -> str:
    """Return runtime Yandex Disk root when available, otherwise env fallback.

    This repository version does not have a persisted app settings table yet;
    keeping this helper centralizes the fallback and matches the runtime-root
    call sites used by the admin bot and Mini App API.
    """
    del session
    return validate_yandex_disk_root(settings.yandex_disk_root)
