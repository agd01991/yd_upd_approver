from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


async def write_audit(
    session: AsyncSession,
    actor_telegram_id: int,
    action: str,
    request_id: int | None = None,
    user_id: int | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_telegram_id=actor_telegram_id,
            action=action,
            request_id=request_id,
            user_id=user_id,
            old_value=old_value,
            new_value=new_value,
        )
    )
