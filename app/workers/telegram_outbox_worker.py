from __future__ import annotations

import asyncio
import logging
import re
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
from app.db.models import (
    FolderRenameRequest,
    FolderRenameRequestStatus,
    TelegramOutbox,
    TelegramOutboxStatus,
    UploadRequest,
    UploadStatus,
    User,
)
from app.db.session import SessionLocal
from app.logging_config import redact_text
from app.utils.formatting import format_upload_card, format_upload_result, format_user_card

logger = logging.getLogger(__name__)
HEALTHCHECK_FILE = Path("/tmp/yd_upd_approver_outbox_heartbeat")  # noqa: S108


class OutboxLeaseLostError(RuntimeError):
    """Raised when this worker no longer owns a telegram outbox row."""


class OutboxHeartbeatError(RuntimeError):
    """Raised when the lease heartbeat cannot safely keep ownership."""


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_error(exc: Exception) -> str:
    try:
        text = redact_text(str(exc)).strip()
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        detail = first_line[:300] or "no error details"
        return f"{exc.__class__.__name__}: {detail}"
    except Exception:
        return f"{exc.__class__.__name__}: no error details"


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


async def _extend_lease_once(
    event_id: int,
    lock_token: str,
    settings: Settings,
) -> None:
    async with SessionLocal() as session:
        result = await session.execute(
            update(TelegramOutbox)
            .where(
                TelegramOutbox.id == event_id,
                TelegramOutbox.status == TelegramOutboxStatus.processing,
                TelegramOutbox.lock_token == lock_token,
            )
            .values(locked_until=_now() + timedelta(seconds=settings.telegram_outbox_lease_seconds))
        )
        await session.commit()
        if result.rowcount != 1:
            raise OutboxLeaseLostError(f"telegram outbox lease lost for event {event_id}")


def _lease_heartbeat_interval(settings: Settings) -> float:
    return max(0.05, settings.telegram_outbox_lease_seconds / 3)


async def _lease_heartbeat(
    event_id: int,
    lock_token: str,
    settings: Settings,
    stop: asyncio.Event,
) -> None:
    interval = _lease_heartbeat_interval(settings)
    while not stop.is_set():
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        try:
            await _extend_lease_once(event_id, lock_token, settings)
        except OutboxLeaseLostError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise OutboxHeartbeatError(
                f"telegram outbox heartbeat failed for event {event_id}: {_safe_error(exc)}"
            ) from exc


async def _await_cancelled(task: asyncio.Task) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _stop_heartbeat_after_send(
    heartbeat_task: asyncio.Task,
    stop: asyncio.Event,
    event_id: int,
) -> None:
    stop.set()
    try:
        await heartbeat_task
    except asyncio.CancelledError:
        raise
    except Exception as cleanup_exc:
        logger.warning(
            "Outbox heartbeat cleanup failed after send success: id=%s category=%s",
            event_id,
            cleanup_exc.__class__.__name__,
        )


async def _stop_heartbeat_after_send_failure(
    heartbeat_task: asyncio.Task,
    stop: asyncio.Event,
) -> None:
    stop.set()
    with suppress(Exception):
        await heartbeat_task


async def _send_with_lease_heartbeat(
    bot: Bot,
    event: TelegramOutbox,
    text: str,
    reply_markup,
    settings: Settings,
):
    lock_token = event.lock_token or ""
    await _extend_lease_once(event.id, lock_token, settings)

    stop = asyncio.Event()
    send_task = asyncio.create_task(
        bot.send_message(event.recipient_telegram_id, text, reply_markup=reply_markup)
    )
    heartbeat_task = asyncio.create_task(_lease_heartbeat(event.id, lock_token, settings, stop))
    try:
        while True:
            await asyncio.wait({send_task, heartbeat_task}, return_when=asyncio.FIRST_COMPLETED)

            if send_task.done():
                try:
                    message = await send_task
                except asyncio.CancelledError:
                    stop.set()
                    await _await_cancelled(heartbeat_task)
                    raise
                except Exception:
                    await _stop_heartbeat_after_send_failure(heartbeat_task, stop)
                    raise

                await _stop_heartbeat_after_send(heartbeat_task, stop, event.id)
                return message

            if heartbeat_task.done():
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    stop.set()
                    await _await_cancelled(send_task)
                    raise
                except Exception as exc:
                    await _await_cancelled(send_task)
                    raise exc

                await _await_cancelled(send_task)
                raise OutboxHeartbeatError(
                    f"telegram outbox heartbeat stopped unexpectedly for event {event.id}"
                )
    except asyncio.CancelledError:
        stop.set()
        await _await_cancelled(send_task)
        await _await_cancelled(heartbeat_task)
        raise
    finally:
        stop.set()
        if not heartbeat_task.done():
            await _await_cancelled(heartbeat_task)
        if not send_task.done():
            await _await_cancelled(send_task)


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


def _valid_upload_attempt_from_event(event: TelegramOutbox, upload: UploadRequest) -> int | None:
    payload = event.payload or {}
    current_attempt = upload.attempt_count or 0
    if "attempt_count" in payload:
        attempt = payload["attempt_count"]
        if not isinstance(attempt, int) or isinstance(attempt, bool):
            return None
        if attempt < 0 or attempt != current_attempt:
            return None
        return attempt

    status = payload.get("status")
    if not isinstance(status, str):
        return None
    audience_by_type = {
        "upload_result_admin": "admin",
        "upload_result_user": "user",
    }
    audience = audience_by_type.get(event.event_type)
    if audience is None:
        return None
    pattern = re.compile(
        r"^upload:(?P<request_id>\d+):attempt:(?P<attempt>\d+):"
        r"(?P<status>[A-Za-z0-9_.-]+):(?P<audience>admin|user):(?P<recipient>\d+)$"
    )
    match = pattern.fullmatch(getattr(event, "dedup_key", "") or "")
    if not match:
        return None
    try:
        request_id = int(match.group("request_id"))
        attempt = int(match.group("attempt"))
        recipient = int(match.group("recipient"))
    except ValueError:
        return None
    if (
        request_id != upload.id
        or attempt != current_attempt
        or match.group("status") != status
        or upload.status.value != status
        or match.group("audience") != audience
        or recipient != event.recipient_telegram_id
    ):
        return None
    return attempt


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
        if event.event_type in {"upload_result_admin", "upload_result_user"}:
            if _valid_upload_attempt_from_event(event, upload) is None:
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
        user = await session.get(User, event.user_id or event.payload.get("user_id"))
        if not user or status not in {"active", "rejected", "blocked"}:
            return None
        if user.status.value != status:
            return None
        text = {
            "active": "Ваш доступ одобрен. Можете отправлять файлы.",
            "rejected": "Ваша заявка на доступ отклонена.",
            "blocked": "Ваш доступ заблокирован администратором.",
        }[status]
        return text, None
    if event.event_type == "admin_folder_rename_pending":
        request_id = event.payload.get("folder_rename_request_id")
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            return None
        rename_request = await session.get(FolderRenameRequest, request_id)
        text = event.payload.get("text")
        if (
            not rename_request
            or rename_request.status != FolderRenameRequestStatus.pending
            or (event.user_id is not None and rename_request.user_id != event.user_id)
            or not isinstance(text, str)
            or not text.strip()
        ):
            return None
        return text, None
    if event.event_type == "folder_rename_result":
        return event.payload.get("text"), None
    return None


ADMIN_TARGETED_EVENT_TYPES = {
    "admin_user_pending",
    "admin_upload_pending",
    "admin_folder_rename_pending",
    "upload_result_admin",
}


async def dispatch_event(bot: Bot, event: TelegramOutbox, settings: Settings) -> None:
    if event.event_type in ADMIN_TARGETED_EVENT_TYPES and event.recipient_telegram_id not in set(
        settings.telegram_admin_ids
    ):
        await mark_discarded(event.id, event.lock_token or "", "recipient is no longer admin")
        return
    async with SessionLocal() as session:
        rendered = await _render(session, event)
    if not rendered or not rendered[0]:
        await mark_discarded(event.id, event.lock_token or "", "obsolete or invalid event")
        return
    msg = await _send_with_lease_heartbeat(bot, event, rendered[0], rendered[1], settings)
    if not await mark_sent(event.id, event.lock_token or "", getattr(msg, "message_id", None)):
        logger.warning("Outbox event lease lost before mark_sent: id=%s", event.id)
        raise OutboxLeaseLostError(f"telegram outbox lease lost for event {event.id}")


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
                await dispatch_event(bot, event, settings)
            except OutboxLeaseLostError as exc:
                logger.warning(
                    "Outbox event ownership lost: id=%s category=%s",
                    event.id,
                    exc.__class__.__name__,
                )
            except OutboxHeartbeatError as exc:
                logger.warning(
                    "Outbox event heartbeat failed: id=%s category=%s",
                    event.id,
                    exc.__class__.__name__,
                )
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
