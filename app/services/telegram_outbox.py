from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TelegramOutbox

_SAFE_KEY = re.compile(r"^[A-Za-z0-9:._-]{1,512}$")


class TelegramEventType(StrEnum):
    admin_user_pending = "admin_user_pending"
    admin_upload_pending = "admin_upload_pending"
    user_moderation_result = "user_moderation_result"
    upload_result_admin = "upload_result_admin"
    upload_result_user = "upload_result_user"
    upload_rejected = "upload_rejected"
    folder_rename_result = "folder_rename_result"
    admin_folder_rename_pending = "admin_folder_rename_pending"


ALLOWED_EVENT_TYPES = {event.value for event in TelegramEventType}
_BLOCKED_PAYLOAD_KEYS = {
    "token",
    "authorization",
    "initdata",
    "init_data",
    "dsn",
    "database_url",
    "local_path",
    "path",
}


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        lower = key.lower()
        if lower in _BLOCKED_PAYLOAD_KEYS or "token" in lower or "authorization" in lower:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[key] = value
    return clean


async def enqueue_telegram_event(
    session: AsyncSession,
    *,
    event_type: str | TelegramEventType,
    recipient_telegram_id: int,
    dedup_key: str,
    payload: dict[str, Any],
    request_id: int | None = None,
    user_id: int | None = None,
) -> bool:
    event_value = event_type.value if isinstance(event_type, TelegramEventType) else event_type
    if event_value not in ALLOWED_EVENT_TYPES:
        raise ValueError("unsupported telegram outbox event type")
    if not _SAFE_KEY.fullmatch(dedup_key):
        raise ValueError("invalid telegram outbox dedup key")
    values = {
        "event_type": event_value,
        "recipient_telegram_id": int(recipient_telegram_id),
        "payload": _validate_payload(payload),
        "dedup_key": dedup_key,
        "request_id": request_id,
        "user_id": user_id,
    }
    dialect = getattr(getattr(session, "bind", None), "dialect", None)
    if getattr(dialect, "name", "") == "postgresql":
        result = await session.execute(
            pg_insert(TelegramOutbox)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[TelegramOutbox.dedup_key])
        )
        return (result.rowcount or 0) == 1
    # Unit-test / SQLite fallback: still tolerate duplicates if fake session cannot execute PG SQL.
    if not hasattr(session, "add"):
        return False
    try:
        session.add(TelegramOutbox(**values))
        await session.flush()
        return True
    except Exception:
        if hasattr(session, "rollback"):
            await session.rollback()
        return False


async def enqueue_admin_upload_pending(session: AsyncSession, settings, upload, user) -> None:
    for admin_id in settings.telegram_admin_ids:
        await enqueue_telegram_event(
            session,
            event_type=TelegramEventType.admin_upload_pending,
            recipient_telegram_id=admin_id,
            dedup_key=f"upload:{upload.id}:submitted:admin:{admin_id}",
            payload={"request_id": upload.id},
            request_id=upload.id,
            user_id=user.id,
        )


async def enqueue_admin_user_pending(session: AsyncSession, settings, user) -> None:
    for admin_id in settings.telegram_admin_ids:
        await enqueue_telegram_event(
            session,
            event_type=TelegramEventType.admin_user_pending,
            recipient_telegram_id=admin_id,
            dedup_key=f"user:{user.id}:pending:admin:{admin_id}",
            payload={"user_id": user.id},
            user_id=user.id,
        )


async def enqueue_upload_result_events(session: AsyncSession, settings, request, user) -> None:
    status = request.status.value
    attempt = request.attempt_count or 0
    for admin_id in settings.telegram_admin_ids if settings else [request.approved_by or 0]:
        if admin_id:
            await enqueue_telegram_event(
                session,
                event_type=TelegramEventType.upload_result_admin,
                recipient_telegram_id=admin_id,
                dedup_key=f"upload:{request.id}:attempt:{attempt}:{status}:admin:{admin_id}",
                payload={"request_id": request.id, "status": status},
                request_id=request.id,
                user_id=request.user_id,
            )
    await enqueue_telegram_event(
        session,
        event_type=TelegramEventType.upload_result_user,
        recipient_telegram_id=user.telegram_id,
        dedup_key=f"upload:{request.id}:attempt:{attempt}:{status}:user:{user.telegram_id}",
        payload={"request_id": request.id, "status": status},
        request_id=request.id,
        user_id=user.id,
    )
