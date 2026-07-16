import base64
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from fastapi import Query
from sqlalchemy import Select, and_, or_

from app.api.errors import ApiError

MAX_CURSOR_LENGTH = 512


def pagination_limit(limit: int = Query(default=25, ge=1, le=100)) -> int:
    return limit


def decode_cursor(cursor: str | None) -> tuple[datetime, int] | None:
    if not cursor:
        return None
    if len(cursor) > MAX_CURSOR_LENGTH:
        raise ApiError(400, "invalid_cursor", "Некорректный cursor пагинации.")
    try:
        raw = base64.urlsafe_b64decode(cursor.encode() + b"=" * (-len(cursor) % 4))
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"created_at", "id"}:
            raise ValueError
        created_at = datetime.fromisoformat(data["created_at"])
        row_id = int(data["id"])
        if row_id < 1:
            raise ValueError
        return created_at, row_id
    except Exception as exc:
        raise ApiError(400, "invalid_cursor", "Некорректный cursor пагинации.") from exc


def encode_cursor(created_at: datetime, row_id: int) -> str:
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "id": row_id},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def apply_cursor(
    stmt: Select, model: Any, cursor: str | None, direction: Literal["desc", "asc"] = "desc"
) -> Select:
    decoded = decode_cursor(cursor)
    if decoded is None:
        return stmt
    created_at, row_id = decoded
    if direction == "desc":
        return stmt.where(
            or_(
                model.created_at < created_at,
                and_(model.created_at == created_at, model.id < row_id),
            )
        )
    return stmt.where(
        or_(model.created_at > created_at, and_(model.created_at == created_at, model.id > row_id))
    )


def page_response(
    rows: list[Any], limit: int, item_fn, *, direction: Literal["desc", "asc"] = "desc"
) -> dict:
    page_rows = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page_rows:
        last = page_rows[-1]
        model = last[0] if isinstance(last, Sequence) and not hasattr(last, "created_at") else last
        next_cursor = encode_cursor(model.created_at, model.id)
    return {
        "items": [item_fn(row) for row in page_rows],
        "limit": limit,
        "has_more": len(rows) > limit,
        "next_cursor": next_cursor,
    }
