import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter

from app.api.routes import user as user_route
from app.api.schemas import FolderRenameRequestCreate
from app.config import Settings
from app.db.models import (
    FolderRenameRequest,
    FolderRenameRequestStatus,
    UploadRequest,
    UploadStatus,
    User,
    UserStatus,
)
from app.services import telegram_outbox as outbox_service
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

    async def fake_extend_lease_once(event_id, lock_token, settings):
        return None

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend_lease_once)
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

    async def fake_extend_lease_once(event_id, lock_token, settings):
        return None

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend_lease_once)
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


class FakeUploadSession(FakeSession):
    def __init__(self, upload=None):
        super().__init__()
        self.upload = upload

    async def get(self, model, key):
        if model is UploadRequest and self.upload and key == self.upload.id:
            return self.upload
        return await super().get(model, key)


def make_upload(status=UploadStatus.failed, attempt_count=2):
    return UploadRequest(
        id=42,
        request_code="REQ-000042",
        user_id=7,
        original_filename="file.txt",
        safe_filename="file.txt",
        size_bytes=10,
        sha256="a" * 64,
        local_path="/tmp/file.txt",  # noqa: S108
        target_folder="disk:/folder",
        target_path="disk:/folder/file.txt",
        status=status,
        attempt_count=attempt_count,
    )


def upload_event(
    payload,
    event_type="upload_result_admin",
    dedup_key=None,
    recipient_telegram_id=99,
    request_id=42,
):
    return SimpleNamespace(
        id=12,
        lock_token="lock",
        event_type=event_type,
        recipient_telegram_id=recipient_telegram_id,
        request_id=request_id,
        user_id=7,
        payload=payload,
        dedup_key=dedup_key
        or f"upload:{request_id}:attempt:{payload.get('attempt_count', 2)}:{payload.get('status', 'failed')}:admin:{recipient_telegram_id}",
    )


async def dispatch_upload_event(
    monkeypatch,
    upload,
    payload,
    event_type="upload_result_admin",
    dedup_key=None,
    recipient_telegram_id=99,
    request_id=42,
):
    discarded = []
    sent = []
    monkeypatch.setattr(worker, "SessionLocal", lambda: FakeUploadSession(upload=upload))

    async def fake_mark_discarded(event_id, lock_token, reason):
        discarded.append((event_id, lock_token, reason))

    async def fake_mark_sent(event_id, lock_token, message_id):
        sent.append((event_id, lock_token, message_id))
        return True

    async def fake_extend_lease_once(event_id, lock_token, settings):
        return None

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend_lease_once)
    monkeypatch.setattr(worker, "mark_discarded", fake_mark_discarded)
    monkeypatch.setattr(worker, "mark_sent", fake_mark_sent)
    bot = FakeBot()
    await worker.dispatch_event(
        bot,
        upload_event(
            payload,
            event_type=event_type,
            dedup_key=dedup_key,
            recipient_telegram_id=recipient_telegram_id,
            request_id=request_id,
        ),
        Settings(telegram_admin_ids=[99]),
    )
    return bot, discarded, sent


async def test_enqueue_upload_result_events_includes_attempt_count(monkeypatch) -> None:
    request = SimpleNamespace(
        id=42,
        user_id=7,
        status=UploadStatus.failed,
        attempt_count=3,
        approved_by=99,
    )
    user = SimpleNamespace(id=7, telegram_id=100)
    settings = Settings(telegram_admin_ids=[99])
    enqueued = []

    async def fake_enqueue_telegram_event(session, **kwargs):
        enqueued.append(kwargs)
        return True

    monkeypatch.setattr(outbox_service, "enqueue_telegram_event", fake_enqueue_telegram_event)
    await outbox_service.enqueue_upload_result_events(object(), settings, request, user)

    assert [item["event_type"] for item in enqueued] == [
        outbox_service.TelegramEventType.upload_result_admin,
        outbox_service.TelegramEventType.upload_result_user,
    ]
    assert enqueued[0]["payload"] == {
        "request_id": 42,
        "status": "failed",
        "attempt_count": 3,
    }
    assert enqueued[1]["payload"] == {
        "request_id": 42,
        "status": "failed",
        "attempt_count": 3,
    }


async def test_stale_upload_attempt_is_discarded_without_sending(monkeypatch) -> None:
    bot, discarded, sent = await dispatch_upload_event(
        monkeypatch,
        make_upload(status=UploadStatus.failed, attempt_count=2),
        {"request_id": 42, "status": "failed", "attempt_count": 1},
    )
    assert bot.sent == []
    assert sent == []
    assert discarded == [(12, "lock", "obsolete or invalid event")]


async def test_current_upload_attempt_is_rendered_and_sent(monkeypatch) -> None:
    bot, discarded, sent = await dispatch_upload_event(
        monkeypatch,
        make_upload(status=UploadStatus.failed, attempt_count=2),
        {"request_id": 42, "status": "failed", "attempt_count": 2},
    )
    assert bot.sent
    assert "REQ-000042" in bot.sent[0][0][1]
    assert discarded == []
    assert sent == [(12, "lock", 123)]


@pytest.mark.parametrize(
    "attempt_count",
    [None, "2", True, -1],
)
async def test_invalid_upload_attempt_payload_is_discarded(monkeypatch, attempt_count) -> None:
    payload = {"request_id": 42, "status": "failed", "attempt_count": attempt_count}
    bot, discarded, sent = await dispatch_upload_event(
        monkeypatch,
        make_upload(status=UploadStatus.failed, attempt_count=2),
        payload,
    )
    assert bot.sent == []
    assert sent == []
    assert discarded == [(12, "lock", "obsolete or invalid event")]


async def test_upload_rejected_does_not_require_attempt_payload(monkeypatch) -> None:
    bot, discarded, sent = await dispatch_upload_event(
        monkeypatch,
        make_upload(status=UploadStatus.rejected, attempt_count=2),
        {"request_id": 42, "status": "rejected"},
        event_type="upload_rejected",
    )
    assert bot.sent
    assert discarded == []
    assert sent == [(12, "lock", 123)]


async def test_upload_result_status_check_still_discards_mismatch(monkeypatch) -> None:
    bot, discarded, sent = await dispatch_upload_event(
        monkeypatch,
        make_upload(status=UploadStatus.uploaded, attempt_count=2),
        {"request_id": 42, "status": "failed", "attempt_count": 2},
    )
    assert bot.sent == []
    assert sent == []
    assert discarded == [(12, "lock", "obsolete or invalid event")]


async def test_legacy_admin_upload_attempt_from_dedup_key_is_sent(monkeypatch) -> None:
    bot, discarded, sent = await dispatch_upload_event(
        monkeypatch,
        make_upload(status=UploadStatus.failed, attempt_count=2),
        {"request_id": 42, "status": "failed"},
        dedup_key="upload:42:attempt:2:failed:admin:99",
    )
    assert bot.sent
    assert discarded == []
    assert sent == [(12, "lock", 123)]


async def test_legacy_user_upload_attempt_from_dedup_key_is_sent(monkeypatch) -> None:
    bot, discarded, sent = await dispatch_upload_event(
        monkeypatch,
        make_upload(status=UploadStatus.uploaded, attempt_count=2),
        {"request_id": 42, "status": "uploaded"},
        event_type="upload_result_user",
        dedup_key="upload:42:attempt:2:uploaded:user:99",
    )
    assert bot.sent[0][0] == (99, "Ваш файл загружен: REQ-000042")
    assert discarded == []
    assert sent == [(12, "lock", 123)]


async def test_legacy_stale_upload_attempt_is_discarded(monkeypatch) -> None:
    bot, discarded, sent = await dispatch_upload_event(
        monkeypatch,
        make_upload(status=UploadStatus.failed, attempt_count=2),
        {"request_id": 42, "status": "failed"},
        dedup_key="upload:42:attempt:1:failed:admin:99",
    )
    assert bot.sent == []
    assert sent == []
    assert discarded == [(12, "lock", "obsolete or invalid event")]


@pytest.mark.parametrize(
    "dedup_key",
    [
        "broken",
        "prefix:upload:42:attempt:2:failed:admin:99",
        "upload:43:attempt:2:failed:admin:99",
        "upload:42:attempt:2:uploaded:admin:99",
        "upload:42:attempt:2:failed:user:99",
        "upload:42:attempt:2:failed:admin:100",
    ],
)
async def test_invalid_legacy_upload_attempt_dedup_key_is_discarded(monkeypatch, dedup_key) -> None:
    bot, discarded, sent = await dispatch_upload_event(
        monkeypatch,
        make_upload(status=UploadStatus.failed, attempt_count=2),
        {"request_id": 42, "status": "failed"},
        dedup_key=dedup_key,
    )
    assert bot.sent == []
    assert sent == []
    assert discarded == [(12, "lock", "obsolete or invalid event")]


async def test_explicit_invalid_attempt_does_not_fall_back_to_legacy_dedup_key(monkeypatch) -> None:
    bot, discarded, sent = await dispatch_upload_event(
        monkeypatch,
        make_upload(status=UploadStatus.failed, attempt_count=2),
        {"request_id": 42, "status": "failed", "attempt_count": "2"},
        dedup_key="upload:42:attempt:2:failed:admin:99",
    )
    assert bot.sent == []
    assert sent == []
    assert discarded == [(12, "lock", "obsolete or invalid event")]


class ExecuteSession:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount
        self.statement = None
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, statement):
        self.statement = statement
        return SimpleNamespace(rowcount=self.rowcount)

    async def commit(self):
        self.committed = True


async def test_extend_lease_once_filters_by_id_status_and_lock_token(monkeypatch) -> None:
    session = ExecuteSession(rowcount=1)
    monkeypatch.setattr(worker, "SessionLocal", lambda: session)

    await worker._extend_lease_once(12, "expected-token", Settings(telegram_admin_ids=[]))

    compiled = str(session.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "telegram_outbox.id = 12" in compiled
    assert "telegram_outbox.status = 'processing'" in compiled
    assert "telegram_outbox.lock_token = 'expected-token'" in compiled
    assert "locked_until" in compiled
    assert session.committed is True


async def test_extend_lease_once_raises_when_rowcount_zero(monkeypatch) -> None:
    monkeypatch.setattr(worker, "SessionLocal", lambda: ExecuteSession(rowcount=0))

    with pytest.raises(worker.OutboxLeaseLostError):
        await worker._extend_lease_once(12, "stale-token", Settings(telegram_admin_ids=[]))


class SlowBot:
    def __init__(self):
        self.sent = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def send_message(self, *args, **kwargs):
        self.sent += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return SimpleNamespace(message_id=456)


def simple_event():
    return SimpleNamespace(
        id=55,
        lock_token="lock",
        event_type="folder_rename_result",
        recipient_telegram_id=99,
        user_id=None,
        payload={"text": "hello"},
    )


async def test_slow_send_extends_lease_and_marks_sent_once(monkeypatch) -> None:
    bot = SlowBot()
    extends = []
    enough_extends = asyncio.Event()
    sent = []
    monkeypatch.setattr(worker, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(worker, "_lease_heartbeat_interval", lambda settings: 0.01)

    async def fake_extend(event_id, lock_token, settings):
        extends.append((event_id, lock_token))
        if len(extends) >= 3:
            enough_extends.set()

    async def fake_mark_sent(event_id, lock_token, message_id):
        sent.append((event_id, lock_token, message_id))
        return True

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)
    monkeypatch.setattr(worker, "mark_sent", fake_mark_sent)
    task = asyncio.create_task(
        worker.dispatch_event(bot, simple_event(), Settings(telegram_admin_ids=[]))
    )
    await bot.started.wait()
    await asyncio.wait_for(enough_extends.wait(), timeout=1)
    bot.release.set()
    await task

    assert bot.sent == 1
    assert len(extends) >= 3
    assert sent == [(55, "lock", 456)]


async def test_lease_lost_before_send_skips_telegram_and_markers(monkeypatch) -> None:
    bot = FakeBot()
    marked = []
    monkeypatch.setattr(worker, "SessionLocal", lambda: FakeSession())

    async def fake_extend(event_id, lock_token, settings):
        raise worker.OutboxLeaseLostError("lost")

    async def fake_mark_sent(*args):
        marked.append(("sent", args))

    async def fake_mark_failed(*args, **kwargs):
        marked.append(("failed", args, kwargs))

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)
    monkeypatch.setattr(worker, "mark_sent", fake_mark_sent)
    monkeypatch.setattr(worker, "mark_failed", fake_mark_failed)

    with pytest.raises(worker.OutboxLeaseLostError):
        await worker.dispatch_event(bot, simple_event(), Settings(telegram_admin_ids=[]))

    assert bot.sent == []
    assert marked == []


async def test_pre_send_db_exception_becomes_heartbeat_error(monkeypatch) -> None:
    bot = FakeBot()
    marked = []
    original = RuntimeError("database connection failed")
    monkeypatch.setattr(worker, "SessionLocal", lambda: FakeSession())

    async def fake_extend(event_id, lock_token, settings):
        raise original

    async def fake_mark_sent(*args):
        marked.append(("sent", args))

    async def fake_mark_failed(*args, **kwargs):
        marked.append(("failed", args, kwargs))

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)
    monkeypatch.setattr(worker, "mark_sent", fake_mark_sent)
    monkeypatch.setattr(worker, "mark_failed", fake_mark_failed)

    with pytest.raises(worker.OutboxHeartbeatError) as exc_info:
        await worker.dispatch_event(bot, simple_event(), Settings(telegram_admin_ids=[]))

    assert exc_info.value.__cause__ is original
    assert bot.sent == []
    assert marked == []


async def test_run_does_not_mark_failed_for_pre_send_heartbeat_error(monkeypatch) -> None:
    event = simple_event()
    stop = asyncio.Event()
    marked_failed = []
    claims = 0
    monkeypatch.setattr(worker, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(worker, "health_heartbeat", lambda stop: asyncio.sleep(0))

    class RunBot(FakeBot):
        def __init__(self, token):
            super().__init__()
            self.session = SimpleNamespace(close=self.close)

        async def close(self):
            return None

    async def fake_claim_next_event(session, settings):
        nonlocal claims
        claims += 1
        if claims == 1:
            return event
        stop.set()
        return None

    async def fake_extend(event_id, lock_token, settings):
        raise RuntimeError("database heartbeat failure")

    async def fake_mark_failed(*args, **kwargs):
        marked_failed.append((args, kwargs))

    monkeypatch.setattr(worker, "Bot", RunBot)
    monkeypatch.setattr(worker, "claim_next_event", fake_claim_next_event)
    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)
    monkeypatch.setattr(worker, "mark_failed", fake_mark_failed)

    await worker.run(
        stop,
        Settings(
            telegram_bot_token="123:abc",
            telegram_admin_ids=[],
        ),
    )

    assert marked_failed == []


async def test_pre_send_refresh_timeout_cancels_inner_refresh(monkeypatch) -> None:
    bot = FakeBot()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_extend(event_id, lock_token, settings):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)
    monkeypatch.setattr(worker, "_lease_refresh_timeout", lambda settings: 0.01)

    with pytest.raises(worker.OutboxHeartbeatError) as exc_info:
        await worker._send_with_lease_heartbeat(
            bot, simple_event(), "hello", None, Settings(telegram_admin_ids=[])
        )

    assert isinstance(exc_info.value.__cause__, TimeoutError)
    assert started.is_set()
    assert cancelled.is_set()
    assert bot.sent == []


async def test_pre_send_lease_lost_is_not_wrapped(monkeypatch) -> None:
    bot = FakeBot()
    lease_lost = worker.OutboxLeaseLostError("lost")

    async def fake_extend(event_id, lock_token, settings):
        raise lease_lost

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)

    with pytest.raises(worker.OutboxLeaseLostError) as exc_info:
        await worker._send_with_lease_heartbeat(
            bot, simple_event(), "hello", None, Settings(telegram_admin_ids=[])
        )

    assert exc_info.value is lease_lost
    assert bot.sent == []


async def test_pre_send_external_cancellation_is_not_wrapped(monkeypatch) -> None:
    bot = FakeBot()
    started = asyncio.Event()
    finalized = asyncio.Event()

    async def fake_extend(event_id, lock_token, settings):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finalized.set()

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)
    task = asyncio.create_task(
        worker._send_with_lease_heartbeat(
            bot, simple_event(), "hello", None, Settings(telegram_admin_ids=[])
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert finalized.is_set()
    assert bot.sent == []


async def test_lease_lost_during_slow_send_cancels_send(monkeypatch) -> None:
    bot = SlowBot()
    calls = 0
    monkeypatch.setattr(worker, "_lease_heartbeat_interval", lambda settings: 0.01)

    async def fake_extend(event_id, lock_token, settings):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise worker.OutboxLeaseLostError("lost")

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)

    with pytest.raises(worker.OutboxLeaseLostError):
        await worker._send_with_lease_heartbeat(
            bot, simple_event(), "hello", None, Settings(telegram_admin_ids=[])
        )

    assert bot.sent == 1
    assert bot.cancelled is True


async def test_hung_periodic_refresh_times_out_and_cancels_send(monkeypatch) -> None:
    bot = SlowBot()
    calls = 0
    hung_started = asyncio.Event()
    hung_cancelled = asyncio.Event()
    marked_sent = []
    monkeypatch.setattr(worker, "_lease_heartbeat_interval", lambda settings: 0.01)
    monkeypatch.setattr(worker, "_lease_refresh_timeout", lambda settings: 0.01)

    async def fake_extend(event_id, lock_token, settings):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        hung_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            hung_cancelled.set()

    async def fake_mark_sent(event_id, lock_token, message_id):
        marked_sent.append((event_id, lock_token, message_id))
        return True

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)
    monkeypatch.setattr(worker, "mark_sent", fake_mark_sent)

    with pytest.raises(worker.OutboxHeartbeatError):
        await worker._send_with_lease_heartbeat(
            bot, simple_event(), "hello", None, Settings(telegram_admin_ids=[])
        )

    assert hung_started.is_set()
    assert hung_cancelled.is_set()
    assert bot.sent == 1
    assert bot.cancelled is True
    assert marked_sent == []


@pytest.mark.parametrize("lease_seconds", [60, 3, 1, 0.3, 0.03])
def test_lease_timing_invariants(lease_seconds) -> None:
    settings = SimpleNamespace(telegram_outbox_lease_seconds=lease_seconds)

    interval = worker._lease_heartbeat_interval(settings)
    refresh_timeout = worker._lease_refresh_timeout(settings)

    assert 0 < interval < lease_seconds
    assert 0 < refresh_timeout < lease_seconds
    assert interval + refresh_timeout < lease_seconds


async def test_mark_sent_false_is_lease_lost(monkeypatch) -> None:
    monkeypatch.setattr(worker, "SessionLocal", lambda: FakeSession())

    async def fake_extend(event_id, lock_token, settings):
        return None

    async def fake_mark_sent(event_id, lock_token, message_id):
        return False

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)
    monkeypatch.setattr(worker, "mark_sent", fake_mark_sent)

    with pytest.raises(worker.OutboxLeaseLostError):
        await worker.dispatch_event(FakeBot(), simple_event(), Settings(telegram_admin_ids=[]))


async def test_successful_send_suppresses_cleanup_heartbeat_failure(monkeypatch, caplog) -> None:
    heartbeat_entered = asyncio.Event()
    finish_send = asyncio.Event()
    sent = []
    extend_calls = 0

    class CoordinatedBot:
        sent = 0

        async def send_message(self, *args, **kwargs):
            self.sent += 1
            await heartbeat_entered.wait()
            finish_send.set()
            return SimpleNamespace(message_id=789)

    async def fake_extend(event_id, lock_token, settings):
        nonlocal extend_calls
        extend_calls += 1
        if extend_calls == 1:
            return None
        heartbeat_entered.set()
        await finish_send.wait()
        raise TimeoutError("temporary db outage")

    async def fake_mark_sent(event_id, lock_token, message_id):
        sent.append((event_id, lock_token, message_id))
        return True

    monkeypatch.setattr(worker, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(worker, "_lease_heartbeat_interval", lambda settings: 0.01)
    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)
    monkeypatch.setattr(worker, "mark_sent", fake_mark_sent)

    bot = CoordinatedBot()
    with caplog.at_level("WARNING", logger=worker.logger.name):
        await worker.dispatch_event(bot, simple_event(), Settings(telegram_admin_ids=[]))

    assert bot.sent == 1
    assert sent == [(55, "lock", 789)]
    assert "Outbox heartbeat cleanup failed after send success" in caplog.text
    assert "id=55" in caplog.text
    assert "category=OutboxHeartbeatError" in caplog.text


async def test_simultaneous_send_success_wins_over_heartbeat_error(monkeypatch) -> None:
    sent = []

    async def fake_extend(event_id, lock_token, settings):
        return None

    async def fake_heartbeat(event_id, lock_token, settings, stop):
        raise worker.OutboxHeartbeatError("heartbeat failed")

    async def fake_mark_sent(event_id, lock_token, message_id):
        sent.append((event_id, lock_token, message_id))
        return True

    monkeypatch.setattr(worker, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)
    monkeypatch.setattr(worker, "_lease_heartbeat", fake_heartbeat)
    monkeypatch.setattr(worker, "mark_sent", fake_mark_sent)

    await worker.dispatch_event(FakeBot(), simple_event(), Settings(telegram_admin_ids=[]))

    assert sent == [(55, "lock", 123)]


async def test_successful_send_attempts_mark_sent_false_after_cleanup_failure(monkeypatch) -> None:
    marked_sent = []
    marked_failed = []

    async def fake_extend(event_id, lock_token, settings):
        return None

    async def fake_heartbeat(event_id, lock_token, settings, stop):
        await stop.wait()
        raise worker.OutboxHeartbeatError("cleanup failed")

    async def fake_mark_sent(event_id, lock_token, message_id):
        marked_sent.append((event_id, lock_token, message_id))
        return False

    async def fake_mark_failed(*args, **kwargs):
        marked_failed.append((args, kwargs))

    monkeypatch.setattr(worker, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)
    monkeypatch.setattr(worker, "_lease_heartbeat", fake_heartbeat)
    monkeypatch.setattr(worker, "mark_sent", fake_mark_sent)
    monkeypatch.setattr(worker, "mark_failed", fake_mark_failed)

    with pytest.raises(worker.OutboxLeaseLostError):
        await worker.dispatch_event(FakeBot(), simple_event(), Settings(telegram_admin_ids=[]))

    assert marked_sent == [(55, "lock", 123)]
    assert marked_failed == []


@pytest.mark.parametrize(
    "exc",
    [
        TelegramRetryAfter(method="sendMessage", message="retry", retry_after=1),
        TelegramNetworkError(method="sendMessage", message="network"),
        TelegramBadRequest(method="sendMessage", message="bad"),
        TimeoutError(),
    ],
)
async def test_send_errors_preserve_original_and_stop_heartbeat(monkeypatch, exc) -> None:
    class ErrorBot:
        async def send_message(self, *args, **kwargs):
            raise exc

    extends = []

    async def fake_extend(event_id, lock_token, settings):
        extends.append(event_id)

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)

    with pytest.raises(type(exc)):
        await worker._send_with_lease_heartbeat(
            ErrorBot(), simple_event(), "hello", None, Settings(telegram_admin_ids=[])
        )

    assert extends == [55]


async def test_send_exception_is_not_replaced_by_cleanup_heartbeat_exception(monkeypatch) -> None:
    original_exc = TelegramNetworkError(method="sendMessage", message="network")

    class ErrorBot:
        async def send_message(self, *args, **kwargs):
            raise original_exc

    async def fake_extend(event_id, lock_token, settings):
        return None

    async def fake_heartbeat(event_id, lock_token, settings, stop):
        await stop.wait()
        raise worker.OutboxHeartbeatError("cleanup failed")

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)
    monkeypatch.setattr(worker, "_lease_heartbeat", fake_heartbeat)

    with pytest.raises(TelegramNetworkError) as exc_info:
        await worker._send_with_lease_heartbeat(
            ErrorBot(), simple_event(), "hello", None, Settings(telegram_admin_ids=[])
        )

    assert exc_info.value is original_exc


async def test_send_with_heartbeat_cancellation_cleans_up(monkeypatch) -> None:
    bot = SlowBot()

    async def fake_extend(event_id, lock_token, settings):
        return None

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)
    task = asyncio.create_task(
        worker._send_with_lease_heartbeat(
            bot, simple_event(), "hello", None, Settings(telegram_admin_ids=[])
        )
    )
    await bot.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bot.cancelled is True


async def test_send_with_heartbeat_cancellation_awaits_heartbeat(monkeypatch) -> None:
    bot = SlowBot()
    heartbeat_cancelled = asyncio.Event()
    heartbeat_finally = asyncio.Event()

    async def fake_extend(event_id, lock_token, settings):
        return None

    async def fake_heartbeat(event_id, lock_token, settings, stop):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            heartbeat_cancelled.set()
            raise
        finally:
            heartbeat_finally.set()

    monkeypatch.setattr(worker, "_extend_lease_once", fake_extend)
    monkeypatch.setattr(worker, "_lease_heartbeat", fake_heartbeat)
    task = asyncio.create_task(
        worker._send_with_lease_heartbeat(
            bot, simple_event(), "hello", None, Settings(telegram_admin_ids=[])
        )
    )
    await bot.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert bot.cancelled is True
    assert heartbeat_cancelled.is_set()
    assert heartbeat_finally.is_set()
