from datetime import UTC, datetime
from secrets import token_hex

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UploadRequest, UploadStatus, User, UserStatus
from app.services.naming import user_folder_for_user


def _norm_folder(path: str) -> str:
    return path.rstrip("/") + "/"


async def lock_user_folder_path(session: AsyncSession, folder: str) -> None:
    if hasattr(session, "execute"):
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:folder))"),
            {"folder": _norm_folder(folder)},
        )


async def find_user_folder_conflict(
    session: AsyncSession, folder: str, *, exclude_user_id: int | None = None
) -> User | None:
    normalized = _norm_folder(folder)
    if not hasattr(session, "scalar") or not hasattr(session, "execute"):
        if not hasattr(session, "scalars"):
            return None
        users = (await session.scalars(select(User))).all()
        for other in users:
            if exclude_user_id is not None and other.id == exclude_user_id:
                continue
            folders = [other.root_folder, *(other.allowed_folders or [])]
            if any(existing and _norm_folder(existing) == normalized for existing in folders):
                return other
        return None
    await lock_user_folder_path(session, normalized)
    stmt = select(User).where(
        (User.root_folder == normalized) | (User.allowed_folders.contains([normalized]))
    )
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    return await session.scalar(stmt.limit(1))


async def get_or_create_user(
    session: AsyncSession, telegram_id: int, username: str | None, full_name: str | None
) -> tuple[User, bool]:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user:
        user.username = username
        user.full_name = full_name
        return user, False
    user = User(
        telegram_id=telegram_id,
        username=username,
        full_name=full_name,
        status=UserStatus.pending,
        allowed_folders=[],
    )
    session.add(user)
    await session.flush()
    return user, True


async def next_request_code(session: AsyncSession) -> str:
    for _ in range(10):
        candidate = f"REQ-{datetime.now(UTC):%Y%m%d}-{token_hex(4).upper()}"
        exists = await session.scalar(
            select(UploadRequest.id).where(UploadRequest.request_code == candidate)
        )
        if not exists:
            return candidate
    msg = "Could not generate unique request code"
    raise RuntimeError(msg)


async def approve_user(session: AsyncSession, user: User, admin_id: int, disk_root: str) -> User:
    folder = user_folder_for_user(disk_root, user)
    conflict = await find_user_folder_conflict(session, folder, exclude_user_id=user.id)
    if conflict:
        raise ValueError("Папка уже назначена другому пользователю")
    user.status = UserStatus.active
    folder = _norm_folder(folder)
    user.root_folder = folder
    user.allowed_folders = [folder]
    user.approved_at = datetime.now(UTC)
    user.approved_by = admin_id
    return user


async def get_user_by_tg(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def create_upload_request(
    session: AsyncSession, request_code: str | None = None, **kwargs: object
) -> UploadRequest:
    upload = UploadRequest(
        request_code=request_code or await next_request_code(session),
        status=UploadStatus.pending_approval,
        **kwargs,
    )
    session.add(upload)
    await session.flush()
    return upload


async def list_user_requests(
    session: AsyncSession, user_id: int, limit: int = 10
) -> list[UploadRequest]:
    return list(
        (
            await session.scalars(
                select(UploadRequest)
                .where(UploadRequest.user_id == user_id)
                .order_by(UploadRequest.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def pending_requests(session: AsyncSession, limit: int = 10) -> list[UploadRequest]:
    return list(
        (
            await session.scalars(
                select(UploadRequest)
                .where(UploadRequest.status == UploadStatus.pending_approval)
                .order_by(UploadRequest.created_at)
                .limit(limit)
            )
        ).all()
    )
