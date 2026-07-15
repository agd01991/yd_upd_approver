import asyncio
import logging
import signal
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db.models import UploadRequest, UploadStatus
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


async def cleanup_temp() -> int:
    settings = get_settings()
    storage = TempStorage(getattr(settings, "temp_storage_dir", Path(".")))
    rejected_cutoff = _now() - timedelta(days=settings.rejected_retention_days)
    deleted = 0
    checked = 0
    async with SessionLocal() as session:
        rows = (
            await session.scalars(
                select(UploadRequest)
                .where(UploadRequest.status.in_(CLEANUP_STATUSES))
                .where(UploadRequest.local_path.is_not(None))
                .where(
                    (UploadRequest.status == UploadStatus.uploaded)
                    | (UploadRequest.created_at < rejected_cutoff)
                )
                .order_by(UploadRequest.id)
                .limit(getattr(settings, "temp_cleanup_batch_size", 100))
            )
        ).all()
        if rows and not hasattr(settings, "temp_storage_dir"):
            storage = TempStorage(Path(rows[0].local_path).parent)
        for request in rows:
            checked += 1
            if request.status in KEEP_STATUSES or not request.local_path:
                continue
            try:
                storage.delete_safe(request.local_path)
                deleted += 1
            except StoragePathError:
                logger.warning(
                    "Skip unsafe temp path during cleanup: request_id=%s",
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
            request.status = UploadStatus.deleted_temp
        await session.commit()
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


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    loop.run_until_complete(run(stop))


if __name__ == "__main__":
    main()
