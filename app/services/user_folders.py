from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import User, UserStatus
from app.services.app_settings import get_yandex_disk_root, set_yandex_disk_root
from app.services.audit import write_audit
from app.services.disk_paths import validate_yandex_disk_root
from app.services.naming import user_folder
from app.services.yandex_disk import YandexDiskClient


async def ensure_user_folder_for_current_root(
    session: AsyncSession,
    user: User,
    settings: Settings,
    client: YandexDiskClient,
) -> str:
    root = (
        await get_yandex_disk_root(session, settings)
        if hasattr(session, "scalar")
        else validate_yandex_disk_root(settings.yandex_disk_root)
    )
    expected = user_folder(root, user.telegram_id, user.full_name, user.username)
    await client.mkdir_recursive(expected)
    if user.root_folder != expected:
        user.root_folder = expected
        user.allowed_folders = [expected]
        await session.flush()
    return expected


async def change_yandex_disk_root_for_active_users(
    session: AsyncSession,
    settings: Settings,
    client: YandexDiskClient,
    root: str,
    actor_telegram_id: int,
) -> str:
    old_root = await get_yandex_disk_root(session, settings)
    normalized = validate_yandex_disk_root(root)
    await client.mkdir_recursive(normalized)
    if hasattr(session, "scalars"):
        users = list(
            (await session.scalars(select(User).where(User.status == UserStatus.active))).all()
        )
    else:
        users = []
    updates: list[tuple[User, str]] = []
    for user in users:
        folder = user_folder(normalized, user.telegram_id, user.full_name, user.username)
        await client.mkdir_recursive(folder)
        updates.append((user, folder))
    for user, folder in updates:
        user.root_folder = folder
        user.allowed_folders = [folder]
    new_root = await set_yandex_disk_root(session, normalized, actor_telegram_id)
    await write_audit(
        session,
        actor_telegram_id=actor_telegram_id,
        action="settings_yandex_disk_root_change",
        old_value={"yandex_disk_root": old_root},
        new_value={"yandex_disk_root": new_root},
    )
    await session.flush()
    return new_root
