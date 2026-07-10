import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl

from fastapi import status

from app.api.errors import ApiError
from app.config import Settings


@dataclass(frozen=True)
class TelegramWebAppUser:
    telegram_id: int
    username: str | None = None
    full_name: str | None = None


def validate_init_data(init_data: str, settings: Settings) -> TelegramWebAppUser:
    if not init_data or not settings.telegram_bot_token:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "authentication_required",
            "Откройте Mini App через Telegram заново.",
        )
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_telegram_init_data",
            "Откройте Mini App через Telegram заново.",
        )
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", settings.telegram_bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_telegram_init_data",
            "Откройте Mini App через Telegram заново.",
        )
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_telegram_init_data",
            "Откройте Mini App через Telegram заново.",
        ) from exc
    if datetime.now(UTC).timestamp() - auth_date > settings.webapp_auth_max_age_seconds:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "telegram_init_data_expired",
            "Сессия Telegram истекла. Откройте Mini App заново.",
        )
    try:
        raw_user = json.loads(pairs["user"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_telegram_init_data",
            "Откройте Mini App через Telegram заново.",
        ) from exc
    if (
        not isinstance(raw_user, dict)
        or "id" not in raw_user
        or not isinstance(raw_user["id"], int)
    ):
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_telegram_init_data",
            "Откройте Mini App через Telegram заново.",
        )
    first = raw_user.get("first_name") or ""
    last = raw_user.get("last_name") or ""
    return TelegramWebAppUser(
        telegram_id=raw_user["id"],
        username=raw_user.get("username"),
        full_name=(f"{first} {last}".strip() or raw_user.get("username")),
    )
