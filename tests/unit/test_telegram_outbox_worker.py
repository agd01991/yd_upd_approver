from types import SimpleNamespace

import pytest

from app.api.routes import user as user_route
from app.api.schemas import FolderRenameRequestCreate
from app.config import Settings
from app.db.models import FolderRenameRequest, FolderRenameRequestStatus, User, UserStatus
from app.workers import telegram_outbox_worker as worker

pytestmark = pytest.mark.anyio


def test_safe_error_handles_empty_timeout() -> None:
    assert worker._safe_error(TimeoutError()) == "TimeoutError: no error details"


class FakeSession:
    def __init__(self, user=None, rename_request=None):
        self.user = user
        self.rename_request = rename_request
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, model, key):
        if model is User and self.user and key == self.user.id:
            return self.user
        if model is FolderRenameRequest and self.rename_request and key == self.rename_request.id:
            return self.rename_request
        return None

    async def scalar(self, _statement):
        return None

    def add(self, obj):
        obj.id = obj.id or 77
        if isinstance(obj, FolderRenameRequest) and obj.status is None:
            obj.status = FolderRenameRequestStatus.pending
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            obj.id = obj.id or 77

    async def commit(self):
        self.committed = True


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
    monkeypatch.setattr(worker, "SessionLocal", lambda: FakeSession(user=user))

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


def rename_event(payload, user_id=1):
    return SimpleNamespace(
        id=10,
        lock_token="lock",
        event_type="admin_folder_rename_pending",
        recipient_telegram_id=99,
        user_id=user_id,
        payload=payload,
    )


async def dispatch_rename_event(monkeypatch, rename_request, payload, user_id=1):
    discarded = []
    sent = []
    monkeypatch.setattr(worker, "SessionLocal", lambda: FakeSession(rename_request=rename_request))

    async def fake_mark_discarded(event_id, lock_token, reason):
        discarded.append((event_id, lock_token, reason))

    async def fake_mark_sent(event_id, lock_token, message_id):
        sent.append((event_id, lock_token, message_id))
        return True

    monkeypatch.setattr(worker, "mark_discarded", fake_mark_discarded)
    monkeypatch.setattr(worker, "mark_sent", fake_mark_sent)
    bot = FakeBot()
    await worker.dispatch_event(
        bot, rename_event(payload, user_id=user_id), Settings(telegram_admin_ids=[99])
    )
    return bot, discarded, sent


async def test_pending_folder_rename_event_is_rendered_and_sent(monkeypatch) -> None:
    req = FolderRenameRequest(id=42, user_id=1, requested_folder_name="new")
    req.status = FolderRenameRequestStatus.pending
    bot, discarded, sent = await dispatch_rename_event(
        monkeypatch, req, {"folder_rename_request_id": 42, "text": "review rename"}
    )
    assert bot.sent[0][0] == (99, "review rename")
    assert discarded == []
    assert sent == [(10, "lock", 123)]


@pytest.mark.parametrize(
    "status",
    [
        FolderRenameRequestStatus.approved,
        FolderRenameRequestStatus.rejected,
        FolderRenameRequestStatus.cancelled,
    ],
)
async def test_processed_folder_rename_event_is_discarded(monkeypatch, status) -> None:
    req = FolderRenameRequest(id=42, user_id=1, requested_folder_name="new")
    req.status = status
    bot, discarded, sent = await dispatch_rename_event(
        monkeypatch, req, {"folder_rename_request_id": 42, "text": "review rename"}
    )
    assert bot.sent == []
    assert sent == []
    assert discarded == [(10, "lock", "obsolete or invalid event")]


@pytest.mark.parametrize(
    "payload",
    [
        {"folder_rename_request_id": 404, "text": "review rename"},
        {"text": "legacy row"},
        {"folder_rename_request_id": "42", "text": "bad id"},
        {"folder_rename_request_id": True, "text": "bool id"},
        {"folder_rename_request_id": 42, "text": ""},
    ],
)
async def test_invalid_or_missing_folder_rename_event_is_discarded(monkeypatch, payload) -> None:
    bot, discarded, sent = await dispatch_rename_event(monkeypatch, None, payload)
    assert bot.sent == []
    assert sent == []
    assert discarded == [(10, "lock", "obsolete or invalid event")]


async def test_folder_rename_event_with_mismatched_user_is_discarded(monkeypatch) -> None:
    req = FolderRenameRequest(id=42, user_id=2, requested_folder_name="new")
    req.status = FolderRenameRequestStatus.pending
    bot, discarded, sent = await dispatch_rename_event(
        monkeypatch, req, {"folder_rename_request_id": 42, "text": "review rename"}, user_id=1
    )
    assert bot.sent == []
    assert sent == []
    assert discarded == [(10, "lock", "obsolete or invalid event")]


async def test_folder_rename_result_does_not_require_pending_status(monkeypatch) -> None:
    event = SimpleNamespace(
        id=11,
        lock_token="lock",
        event_type="folder_rename_result",
        recipient_telegram_id=10,
        user_id=1,
        payload={"text": "approved"},
    )
    sent = []
    monkeypatch.setattr(worker, "SessionLocal", lambda: FakeSession())

    async def fake_mark_sent(event_id, lock_token, message_id):
        sent.append((event_id, lock_token, message_id))
        return True

    monkeypatch.setattr(worker, "mark_sent", fake_mark_sent)
    bot = FakeBot()
    await worker.dispatch_event(bot, event, Settings(telegram_admin_ids=[]))
    assert bot.sent[0][0] == (10, "approved")
    assert sent == [(11, "lock", 123)]


async def test_create_my_folder_rename_request_enqueues_request_id(monkeypatch) -> None:
    user = User(id=5, telegram_id=50, status=UserStatus.active, folder_name="old")
    session = FakeSession(user=user)
    enqueued = []

    async def fake_enqueue_telegram_event(session_arg, **kwargs):
        assert session_arg is session
        enqueued.append(kwargs)

    monkeypatch.setattr(user_route, "enqueue_telegram_event", fake_enqueue_telegram_event)
    result = await user_route.create_my_folder_rename_request(
        FolderRenameRequestCreate(
            requested_folder_name="new",
            contract_number="1",
            contract_date="2026-01-01",
            contract_full_name="User Name",
        ),
        current=(user, False),
        session=session,
        settings=Settings(telegram_admin_ids=[100]),
    )
    assert result == {"id": 77, "status": "pending"}
    assert enqueued[0]["payload"]["folder_rename_request_id"] == 77
    assert enqueued[0]["user_id"] == 5
    assert enqueued[0]["dedup_key"] == "folder-rename:77:pending:admin:100"
