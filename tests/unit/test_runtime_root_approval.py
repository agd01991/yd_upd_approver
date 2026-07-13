import pytest
from fastapi import HTTPException

from app.api.routes.admin import _moderate_user
from app.config import Settings
from app.db.models import User, UserStatus
from app.db.repositories import approve_user

pytestmark = pytest.mark.anyio


class FakeSession:
    def __init__(self, user, setting=None):
        self.user = user
        self.setting = setting
        self.committed = False

    async def get(self, model, key):
        return self.user if model is User and key == self.user.id else None

    async def scalar(self, _stmt):
        return self.setting

    async def commit(self):
        self.committed = True

    def add(self, _row):
        pass


async def test_pending_user_approve_uses_runtime_root() -> None:
    user = User(
        id=1, telegram_id=123, username="User", full_name="Test User", status=UserStatus.pending
    )
    await approve_user(FakeSession(user), user, 99, "disk:/Runtime Root")
    assert user.root_folder.startswith("disk:/Runtime Root/123_user")
    assert user.allowed_folders == [user.root_folder]


async def test_api_approve_active_user_returns_400_without_changes(monkeypatch) -> None:
    user = User(
        id=1,
        telegram_id=123,
        username="user",
        full_name="User",
        status=UserStatus.active,
        root_folder="disk:/Old/123_user/",
        allowed_folders=["disk:/Old/123_user/"],
    )
    session = FakeSession(user)
    with pytest.raises(HTTPException) as exc:
        await _moderate_user(1, "approve", 99, session, Settings(), bot=None)
    assert exc.value.status_code == 400
    assert exc.value.detail == "Пользователь уже обработан."
    assert user.root_folder == "disk:/Old/123_user/"
    assert user.allowed_folders == ["disk:/Old/123_user/"]
    assert session.committed is False


class FakeScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class ConflictSession(FakeSession):
    def __init__(self, user, users):
        super().__init__(user)
        self.users = users

    async def scalars(self, _stmt):
        return FakeScalarResult(self.users)


async def test_pending_user_approve_rejects_folder_conflict() -> None:
    user = User(id=1, telegram_id=123, username="user", full_name="User", status=UserStatus.pending)
    other = User(
        id=2,
        telegram_id=456,
        username="other",
        full_name="Other",
        status=UserStatus.active,
        root_folder="disk:/Runtime Root/123_user/",
        allowed_folders=["disk:/Runtime Root/123_user/"],
    )
    with pytest.raises(ValueError, match="Папка уже назначена другому пользователю"):
        await approve_user(ConflictSession(user, [user, other]), user, 99, "disk:/Runtime Root")
    assert user.status == UserStatus.pending
    assert user.root_folder is None


class ExecuteResult:
    def __init__(self, user):
        self.user = user

    def scalar_one_or_none(self):
        return self.user


class LockingModerationSession(FakeSession):
    def __init__(self, user):
        super().__init__(user)
        self.rolled_back = False

    async def execute(self, _stmt):
        return ExecuteResult(self.user)

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.parametrize(
    ("action", "status"), [("reject", UserStatus.rejected), ("block", UserStatus.blocked)]
)
async def test_api_repeated_terminal_moderation_is_noop(monkeypatch, action, status) -> None:
    user = User(id=1, telegram_id=123, username="user", full_name="User", status=status)
    session = LockingModerationSession(user)
    calls = {"audit": 0, "outbox": 0}

    async def fake_audit(*args, **kwargs):
        calls["audit"] += 1

    async def fake_outbox(*args, **kwargs):
        calls["outbox"] += 1

    def fake_user_json(received_user):
        assert session.rolled_back is False
        return {"id": received_user.id, "status": received_user.status.value}

    monkeypatch.setattr("app.api.routes.admin.write_audit", fake_audit)
    monkeypatch.setattr("app.api.routes.admin.enqueue_telegram_event", fake_outbox)
    monkeypatch.setattr("app.api.routes.admin.user_json", fake_user_json)
    result = await _moderate_user(1, action, 99, session, Settings(), bot=None)
    assert result["status"] == status.value
    assert calls == {"audit": 0, "outbox": 0}
    assert session.rolled_back is True
    assert session.committed is False


@pytest.mark.parametrize(
    ("action", "target"), [("reject", UserStatus.rejected), ("block", UserStatus.blocked)]
)
async def test_api_real_terminal_transition_writes_audit_and_outbox(
    monkeypatch, action, target
) -> None:
    user = User(id=1, telegram_id=123, username="user", full_name="User", status=UserStatus.pending)
    session = LockingModerationSession(user)
    calls = {"audit": 0, "outbox": 0}

    async def fake_audit(*args, **kwargs):
        calls["audit"] += 1

    async def fake_outbox(*args, **kwargs):
        calls["outbox"] += 1

    monkeypatch.setattr("app.api.routes.admin.write_audit", fake_audit)
    monkeypatch.setattr("app.api.routes.admin.enqueue_telegram_event", fake_outbox)
    result = await _moderate_user(1, action, 99, session, Settings(), bot=None)
    assert result["status"] == target.value
    assert calls == {"audit": 1, "outbox": 1}
    assert session.committed is True
