from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import UploadRequest, User
from app.db.repositories import find_user_folder_conflict
from app.services.audit import write_audit
from app.services.naming import join_disk_path, validate_user_folder_name
from app.services.yandex_disk import YandexDiskClient


@dataclass(frozen=True)
class FolderCandidate:
    path: str
    label: str
    is_current: bool = False
    exists: bool | None = None


def _norm(path: str) -> str:
    return path.rstrip("/") + "/"


def _parent(path: str) -> str:
    clean = path.rstrip("/")
    return clean.rsplit("/", 1)[0]


async def get_user_folder_candidates(
    session: AsyncSession, user: User, settings: Settings | None = None
) -> list[FolderCandidate]:
    seen: set[str] = set()
    items: list[FolderCandidate] = []

    def add(path: str | None, label: str, current: bool = False) -> None:
        if not path:
            return
        normalized = _norm(path)
        if normalized in seen:
            return
        seen.add(normalized)
        items.append(FolderCandidate(path=normalized, label=label, is_current=current))

    add(user.root_folder, "Текущая папка", True)
    for folder in user.allowed_folders or []:
        add(folder, "Разрешённая папка", _norm(folder) == _norm(user.root_folder or ""))
    rows = await session.scalars(
        select(UploadRequest.target_folder).where(UploadRequest.user_id == user.id).distinct()
    )
    for folder in rows.all():
        add(folder, "Папка из истории загрузок")
    return items


async def rename_user_folder(
    session: AsyncSession,
    user: User,
    source_folder: str,
    new_folder_name: str,
    actor_telegram_id: int,
    client: YandexDiskClient,
) -> str:
    candidates = await get_user_folder_candidates(session, user)
    allowed = {_norm(c.path) for c in candidates}
    source = _norm(source_folder)
    if source not in allowed:
        raise ValueError("Папка не входит в список папок пользователя")
    safe_name = validate_user_folder_name(new_folder_name)
    target = f"{_parent(source)}/{safe_name}/"
    if target == source:
        raise ValueError("Новое имя совпадает с текущим")
    conflict = await find_user_folder_conflict(session, target, exclude_user_id=user.id)
    if conflict:
        raise ValueError("Папка уже назначена другому пользователю")
    await client.move_resource(source, target, overwrite=False)
    old_allowed = [_norm(p) for p in (user.allowed_folders or [])]
    user.allowed_folders = [target if _norm(p) == source else _norm(p) for p in old_allowed]
    if _norm(user.root_folder or "") == source:
        user.root_folder = target
        user.folder_name = safe_name
    await session.execute(
        update(UploadRequest)
        .where(UploadRequest.user_id == user.id, UploadRequest.target_folder == source)
        .values(target_folder=target)
    )
    uploads = (
        await session.scalars(
            select(UploadRequest).where(
                UploadRequest.user_id == user.id, UploadRequest.target_folder == target
            )
        )
    ).all()
    for upload in uploads:
        upload.target_path = join_disk_path(target, upload.safe_filename)
    await write_audit(
        session,
        actor_telegram_id,
        "user_folder_rename",
        user_id=user.id,
        old_value={"source_folder": source},
        new_value={"target_folder": target, "folder_name": safe_name},
    )
    await session.flush()
    return target
