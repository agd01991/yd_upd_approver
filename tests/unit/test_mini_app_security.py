import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

fastapi = pytest.importorskip("fastapi")
from app.api.errors import ApiError
from app.api.security import validate_init_data
from app.config import Settings


def signed(data: dict, token: str) -> str:
    pairs = {
        k: json.dumps(v, separators=(",", ":")) if isinstance(v, dict) else str(v)
        for k, v in data.items()
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def test_valid_init_data_passes():
    settings = Settings(telegram_bot_token="123:ABC")
    init = signed(
        {"auth_date": int(time.time()), "user": {"id": 42, "first_name": "Ada", "username": "ada"}},
        "123:ABC",
    )
    user = validate_init_data(init, settings)
    assert user.telegram_id == 42
    assert user.username == "ada"


def test_invalid_hash_fails():
    settings = Settings(telegram_bot_token="123:ABC")
    init = signed({"auth_date": int(time.time()), "user": {"id": 42}}, "123:ABC") + "x"
    with pytest.raises(ApiError):
        validate_init_data(init, settings)


def test_expired_auth_date_fails():
    settings = Settings(telegram_bot_token="123:ABC", webapp_auth_max_age_seconds=1)
    init = signed({"auth_date": int(time.time()) - 100, "user": {"id": 42}}, "123:ABC")
    with pytest.raises(ApiError):
        validate_init_data(init, settings)
