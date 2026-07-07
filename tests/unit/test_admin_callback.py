from types import SimpleNamespace

from app.config import Settings
from app.utils.security import ensure_admin_callback


def test_admin_callback_denied_for_user() -> None:
    callback = SimpleNamespace(from_user=SimpleNamespace(id=2))
    assert not ensure_admin_callback(callback, Settings(telegram_admin_ids=[1]))
