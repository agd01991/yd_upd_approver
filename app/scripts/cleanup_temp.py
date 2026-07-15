import asyncio
import logging
import signal
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import exists, func, select

from app.config import get_settings
from app.db.models import TelegramOutbox, TelegramOutboxStatus, UploadRequest, UploadStatus
from app.db.session import SessionLocal
from app.services.storage import StoragePathError, TempStorage
from app.services.telegram_outbox import TelegramEventType

logger = logging.getLogger(__name__)
HEALTHCHECK_FILE = Path("/tmp/yd_upd_approver_cleanup_heartbeat")  # noqa: S108
CLEANUP_STATUSES = {UploadStatus.uploaded, UploadStatus.rejected}
KEEP_STATUSES = {
    UploadStatus.pending_approval,
    UploadStatus.approved,
    UploadStatus.uploading,
    UploadStatus.failed,
}
TERMINAL_UPLOAD_NOTIFICATION_EVENT_TYPES = (
    TelegramEventType.upload_result_admin,
    TelegramEventType.upload_result_user,
    TelegramEventType.upload_rejected,
)
DELIVERABLE_OUTBOX_STATUSES = (
    TelegramOutboxStatus.pending,
    TelegramOutboxStatus.processing,
)


@dataclass(frozen=True)
class CleanupScanState:
    last_id: int = 0
    high_water_id: int | None = None


def _now() -> datetime:
    return datetime.now(UTC)


async def _has_pending_terminal_upload_notification(session, request_id: int) -> bool:
    return bool(
        await session.scalar(
            select(
                exists().where(
                    TelegramOutbox.request_id == request_id,
                    TelegramOutbox.event_type.in_(TERMINAL_UPLOAD_NOTIFICATION_EVENT_TYPES),
                    TelegramOutbox.status.in_(DELIVERABLE_OUTBOX_STATUSES),
                )
            )
        )
    )


async def cleanup_temp_pass(
    scan_state: CleanupScanState | int | None = None,
) -> tuple[int, CleanupScanState | None]:
    """Run one bounded cleanup pass within a finite keyset scan cycle.

    temp_cleanup_batch_size is the maximum number of database rows inspected by this pass,
    including rows skipped because their paths are unsafe or deletion failed. The returned
    state should be passed to the next scheduled pass. ``None`` means the current bounded
    cycle reached its high-water mark and the next pass should start from the beginning
    with a freshly captured high-water mark.
    """
    settings = get_settings()
    storage = TempStorage(getattr(settings, "temp_storage_dir", Path(".")))
    rejected_cutoff = _now() - timedelta(days=settings.rejected_retention_days)
    deleted = 0
    checked = 0
    batch_size = getattr(settings, "temp_cleanup_batch_size", 100)
    if isinstance(scan_state, CleanupScanState):
        cursor = scan_state.last_id
        high_water_id = scan_state.high_water_id
    else:
        cursor = scan_state or 0
        high_water_id = None
    async with SessionLocal() as session:
        if high_water_id is None:
            high_water_id = await session.scalar(select(func.max(UploadRequest.id)))
        if high_water_id is None or cursor >= high_water_id:
            await session.commit()
            next_state = None
            rows = []
        else:
            rows = (
                await session.scalars(
                    select(UploadRequest)
                    .where(UploadRequest.id > cursor)
                    .where(UploadRequest.id <= high_water_id)
                    .where(UploadRequest.status.in_(CLEANUP_STATUSES))
                    .where(UploadRequest.local_path.is_not(None))
                    .where(
                        (UploadRequest.status == UploadStatus.uploaded)
                        | (UploadRequest.created_at < rejected_cutoff)
                    )
                    .order_by(UploadRequest.id)
                    .limit(batch_size)
                )
            ).all()
            if rows and not hasattr(settings, "temp_storage_dir"):
                storage = TempStorage(Path(rows[0].local_path).parent)
            next_cursor = cursor
            for request in rows:
                checked += 1
                request_id = getattr(request, "id", None)
                next_cursor = max(next_cursor, request_id or next_cursor)
                if request.status in KEEP_STATUSES or not request.local_path:
                    continue
                try:
                    storage.delete_safe(request.local_path)
                    deleted += 1
                except StoragePathError:
                    logger.warning(
                        "Skip unsafe temp path during cleanup: request_id=%s category=outside_temp_storage",
                        request_id,
                    )
                    continue
                except Exception as exc:
                    logger.warning(
                        "Temp cleanup delete failed: request_id=%s category=%s",
                        request_id,
                        exc.__class__.__name__,
                    )
                    continue
                if request_id is None or not await _has_pending_terminal_upload_notification(
                    session, request_id
                ):
                    request.status = UploadStatus.deleted_temp
            await session.commit()
            if checked < batch_size or next_cursor >= high_water_id:
                next_state = None
            else:
                next_state = CleanupScanState(last_id=next_cursor, high_water_id=high_water_id)
    deleted += await cleanup_old_parts(
        storage, getattr(settings, "temp_part_retention_seconds", 3600)
    )
    logger.info(
        "temp cleanup pass finished: checked=%s deleted=%s next_cursor=%s high_water_id=%s",
        checked,
        deleted,
        getattr(next_state, "last_id", None),
        getattr(next_state, "high_water_id", None),
    )
    return deleted, next_state


async def cleanup_temp() -> int:
    total_deleted = 0
    state: CleanupScanState | None = None
    while True:
        deleted, state = await cleanup_temp_pass(state)
        total_deleted += deleted
        if state is None:
            return total_deleted


async def cleanup_old_parts(storage: TempStorage, retention_seconds: int) -> int:
    cutoff = _now().timestamp() - retention_seconds
    deleted = 0
    for part in storage.root.glob("**/*.part"):
        try:
            target = storage.validate_inside(part)
            if target.stat().st_mtime > cutoff:
                continue
            target.unlink(missing_ok=True)
            storage._cleanup_empty_request_dir(target)  # noqa: SLF001
            deleted += 1
        except Exception as exc:
            logger.warning("Skipping orphan part cleanup: category=%s", exc.__class__.__name__)
    return deleted


async def health_heartbeat(stop: asyncio.Event) -> None:
    while not stop.is_set():
        HEALTHCHECK_FILE.write_text(str(_now().timestamp()))
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=5)


async def run(stop: asyncio.Event) -> None:
    settings = get_settings()
    health_task = asyncio.create_task(health_heartbeat(stop))
    try:
        state: CleanupScanState | None = None
        while not stop.is_set():
            _deleted, state = await cleanup_temp_pass(state)
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    stop.wait(), timeout=getattr(settings, "temp_cleanup_interval_seconds", 3600)
                )
    finally:
        health_task.cancel()
        await asyncio.gather(health_task, return_exceptions=True)


async def main_async() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    await run(stop)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
