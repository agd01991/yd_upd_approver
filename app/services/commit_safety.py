import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UploadRequest
from app.db.session import SessionLocal
from app.services.storage import TempStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommitOutcome:
    committed: bool
    cancelled: bool = False


async def commit_cancellation_safe(session: AsyncSession) -> CommitOutcome:
    """Run commit so cancellation cannot be mistaken for a rollback."""
    task = asyncio.create_task(session.commit())
    try:
        await asyncio.shield(task)
        return CommitOutcome(committed=True)
    except asyncio.CancelledError:
        try:
            await task
        except BaseException:
            raise
        return CommitOutcome(committed=True, cancelled=True)
    except BaseException:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        raise


async def persisted_upload_uses_path(
    *,
    source_event_key: str,
    user_id: int | None,
    local_path: str | Path,
    request_code: str | None = None,
    session_factory: Callable[[], object] = SessionLocal,
) -> bool | None:
    """Return True/False if fresh-session reconciliation succeeds, None on DB errors."""
    try:
        async with session_factory() as session:
            stmt = select(UploadRequest).where(UploadRequest.source_event_key == source_event_key)
            if user_id is not None:
                stmt = stmt.where(UploadRequest.user_id == user_id)
            upload = await session.scalar(stmt)
            if not upload and request_code:
                upload = await session.scalar(
                    select(UploadRequest).where(UploadRequest.request_code == request_code)
                )
            if not upload:
                return False
            return Path(str(upload.local_path)) == Path(str(local_path))
    except Exception as exc:
        logger.warning(
            "Upload commit reconciliation failed: source_event_key_hash=%s category=%s",
            hash(source_event_key),
            exc.__class__.__name__,
        )
        return None


async def cleanup_staged_if_unowned(
    storage: TempStorage,
    destination: str | Path,
    *,
    source_event_key: str,
    user_id: int | None,
    request_code: str | None = None,
) -> bool:
    owns = await persisted_upload_uses_path(
        source_event_key=source_event_key,
        user_id=user_id,
        local_path=destination,
        request_code=request_code,
    )
    if owns is True or owns is None:
        return False
    storage.delete_safe(destination)
    return True
