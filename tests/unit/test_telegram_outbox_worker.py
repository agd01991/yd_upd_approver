from types import SimpleNamespace

import pytest

from app.config import Settings
from app.db.models import User, UserStatus
from app.workers import telegram_outbox_worker as worker

pytestmark = pytest.mark.anyio


def test_safe_error_handles_empty_timeout() -> None:
    assert worker._safe_error(TimeoutError()) == "TimeoutError: no error details"


class FakeSession:
    def __init__(self, user=None):
        self.user = user

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, model, key):
        return self.user if model is User and self.user and key == self.user.id else None


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        return SimpleNamespace(message_id=123)


async def test_stale_user_moderation_result_is_discarded(monkeypatch) -> None:
    user = User(id=1, telegram_id=10, status=UserStatus.blocked)
    event = SimpleNamespace(
        id=5,
        lock_token="lock",
        event_type="user_moderation_result",
        recipient_telegram_id=10,
        user_id=1,
        payload={"status": "active", "user_id": 1},
    )
    discarded = []
    monkeypatch.setattr(worker, "SessionLocal", lambda: FakeSession(user))

    async def fake_mark_discarded(event_id, lock_token, reason):
        discarded.append((event_id, lock_token, reason))

    monkeypatch.setattr(worker, "mark_discarded", fake_mark_discarded)
    bot = FakeBot()
    await worker.dispatch_event(bot, event, Settings(telegram_admin_ids=[]))
    assert bot.sent == []
    assert discarded == [(5, "lock", "obsolete or invalid event")]


async def test_admin_event_for_removed_admin_is_discarded(monkeypatch) -> None:
    event = SimpleNamespace(
        id=6,
        lock_token="lock",
        event_type="admin_upload_pending",
        recipient_telegram_id=99,
        user_id=None,
        payload={},
    )
    discarded = []

    async def fake_mark_discarded(event_id, lock_token, reason):
        discarded.append((event_id, lock_token, reason))

    monkeypatch.setattr(worker, "mark_discarded", fake_mark_discarded)
    bot = FakeBot()
    await worker.dispatch_event(bot, event, Settings(telegram_admin_ids=[1]))
    assert bot.sent == []
    assert discarded == [(6, "lock", "recipient is no longer admin")]
