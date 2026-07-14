from types import SimpleNamespace

import pytest

from app.bot.callbacks import UserModerationCallback
from app.bot.handlers import admin as admin_handlers
from app.config import Settings
from app.db.models import User, UserStatus
from app.utils.security import ensure_admin_callback

pytestmark = pytest.mark.anyio


def test_admin_callback_denied_for_user() -> None:
    callback = SimpleNamespace(from_user=SimpleNamespace(id=2))
    assert not ensure_admin_callback(callback, Settings(telegram_admin_ids=[1]))


class CallbackExecuteResult:
    def __init__(self, user):
        self.user = user

    def scalar_one_or_none(self):
        return self.user


class CallbackSession:
    def __init__(self, user):
        self.user = user
        self.committed = False
        self.rolled_back = False

    async def execute(self, _stmt):
        return CallbackExecuteResult(self.user)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


class FakeCallback:
    def __init__(self):
        self.from_user = SimpleNamespace(id=99)
        self.answers = []

    async def answer(self, text, show_alert=False):
        self.answers.append((text, show_alert))


@pytest.mark.parametrize(
    ("action", "status", "message"),
    [
        ("reject", UserStatus.rejected, "Пользователь уже отклонён"),
        ("block", UserStatus.blocked, "Пользователь уже заблокирован"),
    ],
)
async def test_telegram_repeated_terminal_moderation_is_noop(
    monkeypatch, action, status, message
) -> None:
    user = User(id=1, telegram_id=123, status=status)
    calls = {"audit": 0, "outbox": 0}

    async def fake_audit(*args, **kwargs):
        calls["audit"] += 1

    async def fake_outbox(*args, **kwargs):
        calls["outbox"] += 1

    monkeypatch.setattr(admin_handlers, "write_audit", fake_audit)
    monkeypatch.setattr(admin_handlers, "enqueue_telegram_event", fake_outbox)
    callback = FakeCallback()
    session = CallbackSession(user)
    await admin_handlers.user_callback(
        callback,
        UserModerationCallback(action=action, user_id=1),
        SimpleNamespace(),
        session,
        Settings(telegram_admin_ids=[99]),
    )
    assert callback.answers == [(message, False)]
    assert calls == {"audit": 0, "outbox": 0}
    assert session.rolled_back is True
    assert session.committed is False
