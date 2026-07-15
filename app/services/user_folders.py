from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import User, UserStatus
from app.services.app_settings import get_yandex_disk_root, set_yandex_disk_root
from app.services.audit import write_audit
from app.services.disk_paths import validate_yandex_disk_root
from app.services.naming import user_folder_for_user
from app.services.yandex_disk import YandexDiskClient


def _folder_with_trailing_slash(root: str, basename: str) -> str:
    return f"{root.rstrip('/')}/{basename}/"


def _user_folder_basename(user: User) -> str | None:
    folder = (user.root_folder or "").strip().rstrip("/")
    if not folder:
        return None
    basename = folder.rsplit("/", 1)[-1]
    if not basename or basename in {".", ".."}:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in basename):
        return None
    return basename


def _is_folder_inside_root(folder: str | None, root: str) -> bool:
    if not folder:
        return False
    normalized_folder = folder.rstrip("/")
    normalized_root = root.rstrip("/")
    return normalized_folder.startswith(f"{normalized_root}/")


def stable_user_folder_for_root(root: str, user: User) -> str:
    basename = _user_folder_basename(user)
    if basename is None:
        return user_folder_for_user(root, user)
    return _folder_with_trailing_slash(root, basename)


async def resolve_user_folder_for_current_root(
    session: AsyncSession, user: User, settings: Settings
) -> str:
    root = (
        await get_yandex_disk_root(session, settings)
        if hasattr(session, "scalar")
        else validate_yandex_disk_root(settings.yandex_disk_root)
    )
    expected = (
        user.root_folder
        if _is_folder_inside_root(user.root_folder, root)
        else stable_user_folder_for_root(root, user)
    )
    if user.root_folder != expected:
        user.root_folder = expected
        user.allowed_folders = [expected]
        if hasattr(session, "flush"):
            await session.flush()
    return expected


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
    expected = (
        user.root_folder
        if _is_folder_inside_root(user.root_folder, root)
        else stable_user_folder_for_root(root, user)
    )
    await client.mkdir_recursive(expected)
    if user.root_folder != expected:
        user.root_folder = expected
        user.allowed_folders = [expected]
        if hasattr(session, "flush"):
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
    if normalized == old_root:
        return old_root
    if hasattr(session, "scalars"):
        users = list(
            (await session.scalars(select(User).where(User.status == UserStatus.active))).all()
        )
    else:
        users = []
    updates: list[tuple[User, str]] = []
    for user in users:
        folder = stable_user_folder_for_root(normalized, user)
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
    if hasattr(session, "flush"):
        await session.flush()
    return new_root
