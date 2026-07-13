from __future__ import annotations

import asyncio
import logging
import secrets
import signal
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import upload_keyboard, user_moderation_keyboard
from app.config import Settings, get_settings
from app.db.models import TelegramOutbox, TelegramOutboxStatus, UploadRequest, UploadStatus, User
from app.db.session import SessionLocal
from app.utils.formatting import format_upload_card, format_upload_result, format_user_card

logger = logging.getLogger(__name__)
HEALTHCHECK_FILE = Path("/tmp/yd_upd_approver_outbox_heartbeat")  # noqa: S108


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_error(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {str(exc).splitlines()[0][:300]}"


def _backoff(settings: Settings, attempts: int, retry_after: int | None = None) -> datetime:
    if retry_after is not None:
        return _now() + timedelta(seconds=max(1, retry_after))
    seconds = min(
        settings.telegram_outbox_max_retry_seconds,
        settings.telegram_outbox_base_retry_seconds * (2 ** max(0, attempts - 1)),
    )
    return _now() + timedelta(
        seconds=seconds + secrets.randbelow(max(1, int(min(seconds, 5) * 1000))) / 1000
    )


async def claim_next_event(session: AsyncSession, settings: Settings) -> TelegramOutbox | None:
    now = _now()
    result = await session.execute(
        select(TelegramOutbox)
        .where(
            or_(
                (TelegramOutbox.status == TelegramOutboxStatus.pending)
                & (TelegramOutbox.next_attempt_at <= now),
                (TelegramOutbox.status == TelegramOutboxStatus.processing)
                & (TelegramOutbox.locked_until < now),
            )
        )
        .order_by(TelegramOutbox.next_attempt_at.asc(), TelegramOutbox.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        await session.rollback()
        return None
    row.status = TelegramOutboxStatus.processing
    row.lock_token = uuid4().hex
    row.locked_until = now + timedelta(seconds=settings.telegram_outbox_lease_seconds)
    row.last_attempt_at = now
    row.attempt_count = (row.attempt_count or 0) + 1
    await session.commit()
    return row


async def mark_sent(event_id: int, lock_token: str, message_id: int | None) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(
            update(TelegramOutbox)
            .where(TelegramOutbox.id == event_id, TelegramOutbox.lock_token == lock_token)
            .values(
                status=TelegramOutboxStatus.sent,
                sent_at=_now(),
                telegram_message_id=message_id,
                lock_token=None,
                locked_until=None,
                last_error=None,
            )
        )
        await session.commit()
        return result.rowcount == 1


async def mark_discarded(event_id: int, lock_token: str, reason: str) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(TelegramOutbox)
            .where(TelegramOutbox.id == event_id, TelegramOutbox.lock_token == lock_token)
            .values(
                status=TelegramOutboxStatus.discarded,
                lock_token=None,
                locked_until=None,
                last_error=reason[:500],
            )
        )
        await session.commit()


async def mark_failed(
    event: TelegramOutbox,
    settings: Settings,
    exc: Exception,
    permanent: bool = False,
    retry_after: int | None = None,
) -> None:
    status = (
        TelegramOutboxStatus.dead
        if permanent or event.attempt_count >= settings.telegram_outbox_max_attempts
        else TelegramOutboxStatus.pending
    )
    async with SessionLocal() as session:
        await session.execute(
            update(TelegramOutbox)
            .where(TelegramOutbox.id == event.id, TelegramOutbox.lock_token == event.lock_token)
            .values(
                status=status,
                next_attempt_at=_backoff(settings, event.attempt_count, retry_after),
                lock_token=None,
                locked_until=None,
                last_error=_safe_error(exc),
            )
        )
        await session.commit()


async def _render(session: AsyncSession, event: TelegramOutbox):
    if event.event_type == "admin_user_pending":
        user = await session.get(User, event.user_id or event.payload.get("user_id"))
        if not user or user.status.value != "pending":
            return None
        return format_user_card(user), user_moderation_keyboard(user.id)
    if event.event_type == "admin_upload_pending":
        upload = await session.get(
            UploadRequest, event.request_id or event.payload.get("request_id")
        )
        user = await session.get(User, upload.user_id) if upload else None
        if not upload or not user or upload.status != UploadStatus.pending_approval:
            return None
        return format_upload_card(upload, user), upload_keyboard(upload)
    if event.event_type in {"upload_result_admin", "upload_result_user", "upload_rejected"}:
        upload = await session.get(
            UploadRequest, event.request_id or event.payload.get("request_id")
        )
        if not upload:
            return None
        expected = event.payload.get("status")
        if expected and upload.status.value != expected:
            return None
        if event.event_type == "upload_result_user":
            if upload.status == UploadStatus.uploaded:
                return f"Ваш файл загружен: {upload.request_code}", None
            if upload.status == UploadStatus.failed:
                return (
                    f"Загрузка файла {upload.request_code} не удалась. {upload.error_message or ''}".strip(),
                    None,
                )
        markup = upload_keyboard(upload) if upload.status == UploadStatus.failed else None
        return format_upload_result(upload), markup
    if event.event_type == "user_moderation_result":
        status = event.payload.get("status")
        text = {
            "active": "Ваш доступ одобрен. Можете отправлять файлы.",
            "rejected": "Ваша заявка на доступ отклонена.",
            "blocked": "Ваш доступ заблокирован администратором.",
        }.get(status)
        return (text, None) if text else None
    if event.event_type in {"folder_rename_result", "admin_folder_rename_pending"}:
        return event.payload.get("text"), None
    return None


async def dispatch_event(bot: Bot, event: TelegramOutbox) -> None:
    async with SessionLocal() as session:
        rendered = await _render(session, event)
    if not rendered or not rendered[0]:
        await mark_discarded(event.id, event.lock_token or "", "obsolete or invalid event")
        return
    msg = await bot.send_message(event.recipient_telegram_id, rendered[0], reply_markup=rendered[1])
    await mark_sent(event.id, event.lock_token or "", getattr(msg, "message_id", None))


async def health_heartbeat(stop: asyncio.Event) -> None:
    while not stop.is_set():
        HEALTHCHECK_FILE.write_text(str(_now().timestamp()))
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=5)


async def run(stop: asyncio.Event, settings: Settings) -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required for telegram outbox worker")
    bot = Bot(settings.telegram_bot_token)
    health_task = asyncio.create_task(health_heartbeat(stop))
    try:
        while not stop.is_set():
            async with SessionLocal() as session:
                event = await claim_next_event(session, settings)
            if event is None:
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        stop.wait(), timeout=settings.telegram_outbox_poll_seconds
                    )
                continue
            try:
                await dispatch_event(bot, event)
            except TelegramRetryAfter as exc:
                await mark_failed(event, settings, exc, retry_after=exc.retry_after)
            except (TelegramForbiddenError, TelegramBadRequest) as exc:
                await mark_failed(event, settings, exc, permanent=True)
            except (TelegramNetworkError, TelegramServerError, TimeoutError) as exc:
                await mark_failed(event, settings, exc)
            except Exception as exc:
                logger.warning(
                    "Outbox event failed: id=%s category=%s", event.id, exc.__class__.__name__
                )
                await mark_failed(event, settings, exc)
    finally:
        stop.set()
        await health_task
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
