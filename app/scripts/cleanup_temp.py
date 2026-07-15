import asyncio
import logging
import signal
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import exists, select

from app.config import get_settings
from app.db.models import TelegramOutbox, TelegramOutboxStatus, UploadRequest, UploadStatus
from app.db.session import SessionLocal
from app.services.storage import StoragePathError, TempStorage

logger = logging.getLogger(__name__)
HEALTHCHECK_FILE = Path("/tmp/yd_upd_approver_cleanup_heartbeat")  # noqa: S108
CLEANUP_STATUSES = {UploadStatus.uploaded, UploadStatus.rejected}
KEEP_STATUSES = {
    UploadStatus.pending_approval,
    UploadStatus.approved,
    UploadStatus.uploading,
    UploadStatus.failed,
}


def _now() -> datetime:
    return datetime.now(UTC)


async def _has_deliverable_upload_result_outbox(session, request_id: int) -> bool:
    return bool(
        await session.scalar(
            select(
                exists().where(
                    TelegramOutbox.request_id == request_id,
                    TelegramOutbox.event_type.in_(("upload_result_admin", "upload_result_user")),
                    TelegramOutbox.status.in_(
                        (TelegramOutboxStatus.pending, TelegramOutboxStatus.processing)
                    ),
                )
            )
        )
    )


async def cleanup_temp() -> int:
    settings = get_settings()
    storage = TempStorage(getattr(settings, "temp_storage_dir", Path(".")))
    rejected_cutoff = _now() - timedelta(days=settings.rejected_retention_days)
    deleted = 0
    checked = 0
    batch_size = getattr(settings, "temp_cleanup_batch_size", 100)
    last_id = 0
    async with SessionLocal() as session:
        while True:
            rows = (
                await session.scalars(
                    select(UploadRequest)
                    .where(UploadRequest.id > last_id)
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
            if not rows:
                break
            for request in rows:
                checked += 1
                last_id = max(last_id, getattr(request, "id", last_id))
                if request.status in KEEP_STATUSES or not request.local_path:
                    continue
                try:
                    storage.delete_safe(request.local_path)
                    deleted += 1
                except StoragePathError:
                    logger.warning(
                        "Skip unsafe temp path during cleanup: request_id=%s category=outside_temp_storage",
                        getattr(request, "id", None),
                    )
                    continue
                except Exception as exc:
                    logger.warning(
                        "Temp cleanup delete failed: request_id=%s category=%s",
                        request.id,
                        exc.__class__.__name__,
                    )
                    continue
                request_id = getattr(request, "id", None)
                if request_id is None or not await _has_deliverable_upload_result_outbox(
                    session, request_id
                ):
                    request.status = UploadStatus.deleted_temp
            await session.commit()
            if len(rows) < batch_size:
                break
    deleted += await cleanup_old_parts(
        storage, getattr(settings, "temp_part_retention_seconds", 3600)
    )
    logger.info("temp cleanup finished: checked=%s deleted=%s", checked, deleted)
    return deleted


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
        while not stop.is_set():
            await cleanup_temp()
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
