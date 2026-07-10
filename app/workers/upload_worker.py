import asyncio
import logging
import signal
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from aiogram import Bot
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import UploadMode, UploadRequest, UploadStatus, User
from app.db.session import SessionLocal
from app.services.audit import write_audit
from app.services.storage import TempStorage
from app.services.yandex_disk import (
    ConflictError,
    InsufficientStorageError,
    YandexAuthError,
    YandexDiskClient,
    YandexNetworkError,
)

logger = logging.getLogger(__name__)
HEALTHCHECK_FILE = Path("/tmp/yd_upd_approver_worker_heartbeat")  # noqa: S108


@dataclass(frozen=True)
class UploadJob:
    id: int
    request_code: str
    user_id: int
    admin_id: int
    local_path: str
    target_folder: str
    target_path: str
    safe_filename: str
    size_bytes: int
    sha256: str
    upload_mode: UploadMode
    worker_token: str


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, ConflictError):
        return "Name conflict on Yandex Disk"
    if isinstance(exc, InsufficientStorageError):
        return "На Яндекс.Диске недостаточно свободного места."
    if isinstance(exc, (YandexAuthError, YandexNetworkError)):
        return "Яндекс.Диск временно недоступен. Повторите попытку позже."
    if isinstance(exc, FileNotFoundError):
        return "Temporary file not found. User must upload the file again."
    return "Не удалось загрузить файл. Повторите попытку позже."


def _is_under_temp(path: str, settings: Settings) -> bool:
    try:
        Path(path).resolve().relative_to(settings.temp_storage_dir.resolve())
    except ValueError:
        return False
    return True


def _remote_sha256(info: dict) -> str | None:
    return info.get("sha256") or info.get("custom_properties", {}).get("sha256")


async def recover_stale_jobs(session: AsyncSession) -> int:
    now = _now()
    result = await session.execute(
        update(UploadRequest)
        .where(
            UploadRequest.status == UploadStatus.uploading,
            or_(UploadRequest.lease_expires_at.is_(None), UploadRequest.lease_expires_at < now),
        )
        .values(
            status=UploadStatus.approved,
            worker_token=None,
            lease_expires_at=None,
            queued_at=now,
        )
        .returning(UploadRequest.id, UploadRequest.approved_by)
    )
    rows = result.all()
    for request_id, actor in rows:
        if actor is not None:
            await write_audit(
                session,
                actor_telegram_id=actor,
                action="upload_recovered",
                request_id=request_id,
                new_value={"status": UploadStatus.approved.value, "queued_at": now.isoformat()},
            )
    await session.commit()
    if rows:
        logger.warning("Recovered %s stale upload job(s)", len(rows))
    return len(rows)


async def claim_next_job(session: AsyncSession, settings: Settings) -> UploadJob | None:
    now = _now()
    token = uuid4().hex
    result = await session.execute(
        select(UploadRequest)
        .where(UploadRequest.status == UploadStatus.approved)
        .order_by(UploadRequest.queued_at.asc().nulls_last(), UploadRequest.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    request = result.scalar_one_or_none()
    if request is None:
        await session.rollback()
        return None
    request.status = UploadStatus.uploading
    request.worker_token = token
    request.attempt_count = (request.attempt_count or 0) + 1
    request.last_attempt_at = now
    request.lease_expires_at = now + timedelta(seconds=settings.upload_worker_lease_seconds)
    await write_audit(
        session,
        actor_telegram_id=request.approved_by or 0,
        action="upload_started",
        request_id=request.id,
        user_id=request.user_id,
        new_value={"status": request.status.value, "attempt_count": request.attempt_count},
    )
    await session.commit()
    return UploadJob(
        id=request.id,
        request_code=request.request_code,
        user_id=request.user_id,
        admin_id=request.approved_by or 0,
        local_path=request.local_path,
        target_folder=request.target_folder,
        target_path=request.target_path,
        safe_filename=request.safe_filename,
        size_bytes=request.size_bytes,
        sha256=request.sha256,
        upload_mode=request.upload_mode or UploadMode.normal,
        worker_token=token,
    )


async def heartbeat(job: UploadJob, settings: Settings, stop: asyncio.Event) -> None:
    while not stop.is_set():
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.upload_worker_heartbeat_seconds)
        if stop.is_set():
            break
        async with SessionLocal() as session:
            result = await session.execute(
                update(UploadRequest)
                .where(
                    UploadRequest.id == job.id,
                    UploadRequest.status == UploadStatus.uploading,
                    UploadRequest.worker_token == job.worker_token,
                )
                .values(
                    lease_expires_at=_now()
                    + timedelta(seconds=settings.upload_worker_lease_seconds)
                )
            )
            await session.commit()
            if result.rowcount != 1:
                logger.warning("Lost ownership for upload job %s", job.id)
                stop.set()


async def health_heartbeat(stop: asyncio.Event) -> None:
    while not stop.is_set():
        HEALTHCHECK_FILE.write_text(str(_now().timestamp()))
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=5)


async def _remote_matches(client: YandexDiskClient, target_path: str, job: UploadJob) -> bool:
    try:
        info = await client.get_info(target_path)
    except FileNotFoundError:
        return False
    remote_hash = _remote_sha256(info)
    return (
        info.get("size") == job.size_bytes and remote_hash is not None and remote_hash == job.sha256
    )


async def finalize_success(job: UploadJob, target_path: str) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(
            select(UploadRequest).where(UploadRequest.id == job.id).with_for_update()
        )
        request = result.scalar_one_or_none()
        if (
            not request
            or request.status != UploadStatus.uploading
            or request.worker_token != job.worker_token
        ):
            await session.rollback()
            logger.warning("Skip success finalize for upload job %s: ownership lost", job.id)
            return False
        request.status = UploadStatus.uploaded
        request.uploaded_at = _now()
        request.error_message = None
        request.worker_token = None
        request.lease_expires_at = None
        request.target_path = target_path
        await write_audit(
            session,
            job.admin_id,
            "upload_uploaded",
            request_id=job.id,
            user_id=job.user_id,
            new_value={"status": request.status.value, "target_path": target_path},
        )
        await session.commit()
        return True


async def finalize_failure(job: UploadJob, message: str) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(
            select(UploadRequest).where(UploadRequest.id == job.id).with_for_update()
        )
        request = result.scalar_one_or_none()
        if (
            not request
            or request.status != UploadStatus.uploading
            or request.worker_token != job.worker_token
        ):
            await session.rollback()
            logger.warning("Skip failure finalize for upload job %s: ownership lost", job.id)
            return False
        request.status = UploadStatus.failed
        request.error_message = message
        request.worker_token = None
        request.lease_expires_at = None
        await write_audit(
            session,
            job.admin_id,
            "upload_failed",
            request_id=job.id,
            user_id=job.user_id,
            new_value={"status": request.status.value, "error_message": message},
        )
        await session.commit()
        return True


async def notify_result(
    bot: Bot | None, job: UploadJob, status: UploadStatus, message: str | None = None
) -> None:
    if bot is None:
        return
    try:
        async with SessionLocal() as session:
            user = await session.get(User, job.user_id)
        if status == UploadStatus.uploaded:
            text = f"Ваш файл загружен: {job.request_code}"
        else:
            text = f"Загрузка файла {job.request_code} не удалась. {message or ''}".strip()
        if user:
            await bot.send_message(user.telegram_id, text)
        await bot.send_message(
            job.admin_id,
            f"Итог загрузки {job.request_code}: {status.value}{': ' + message if message else ''}",
        )
    except Exception:
        logger.exception("Failed to send upload notification for job %s", job.id)


async def process_job(job: UploadJob, settings: Settings, bot: Bot | None = None) -> None:
    stop = asyncio.Event()
    hb = asyncio.create_task(heartbeat(job, settings, stop))
    target = job.target_path
    try:
        path = Path(job.local_path)
        if not path.is_file() or not _is_under_temp(job.local_path, settings):
            raise FileNotFoundError(job.local_path)
        client = YandexDiskClient(settings.yandex_disk_token)
        try:
            if await _remote_matches(client, target, job):
                uploaded = True
            else:
                await client.mkdir_recursive(job.target_folder)
                overwrite = job.upload_mode == UploadMode.overwrite
                await client.upload_file(job.local_path, target, overwrite=overwrite)
                uploaded = True
        finally:
            await client.close()
        if uploaded and await finalize_success(job, target):
            TempStorage.delete(job.local_path)
            await notify_result(bot, job, UploadStatus.uploaded)
    except asyncio.CancelledError:
        logger.info("Upload job %s cancelled; lease will expire", job.id)
        raise
    except Exception as exc:
        logger.exception("Upload job %s failed safely", job.id)
        message = _safe_error(exc)
        if await finalize_failure(job, message):
            await notify_result(bot, job, UploadStatus.failed, message)
    finally:
        stop.set()
        await hb


async def upload_approved_request(
    session: AsyncSession, request: UploadRequest, client: YandexDiskClient, overwrite: bool = False
) -> None:
    """Compatibility helper for legacy unit tests; API and bot do not call this."""
    if request.status != UploadStatus.approved:
        request.status = UploadStatus.failed
        request.error_message = "Недопустимое состояние заявки."
        await session.flush()
        return
    if not request.local_path or not Path(request.local_path).exists():
        request.status = UploadStatus.failed
        request.error_message = _safe_error(FileNotFoundError(request.local_path))
        await session.flush()
        return
    request.status = UploadStatus.uploading
    request.error_message = None
    await session.flush()
    try:
        await client.mkdir_recursive(request.target_folder)
        await client.upload_file(request.local_path, request.target_path, overwrite=overwrite)
    except Exception as exc:
        request.status = UploadStatus.failed
        request.error_message = _safe_error(exc)
        await session.flush()
        return
    request.status = UploadStatus.uploaded
    request.uploaded_at = _now()
    TempStorage.delete(request.local_path)
    await session.flush()


async def run(stop: asyncio.Event, settings: Settings) -> None:
    bot = Bot(settings.telegram_bot_token) if settings.telegram_bot_token else None
    health_task = asyncio.create_task(health_heartbeat(stop))
    try:
        while not stop.is_set():
            async with SessionLocal() as session:
                await recover_stale_jobs(session)
            async with SessionLocal() as session:
                job = await claim_next_job(session, settings)
            if job is None:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=settings.upload_worker_poll_seconds)
                continue
            await process_job(job, settings, bot)
    finally:
        stop.set()
        await health_task
        if bot is not None:
            await bot.session.close()


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    await run(stop, settings)


if __name__ == "__main__":
    asyncio.run(main())
