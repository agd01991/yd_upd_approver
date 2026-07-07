from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UploadRequest, UploadStatus, User, UserStatus
from app.services.naming import user_folder


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
    max_id = await session.scalar(select(func.max(UploadRequest.id)))
    return f"REQ-{(max_id or 0) + 1:06d}"


async def approve_user(session: AsyncSession, user: User, admin_id: int, disk_root: str) -> User:
    folder = user_folder(disk_root, user.telegram_id, user.full_name, user.username)
    user.status = UserStatus.active
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
