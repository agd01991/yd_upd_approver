from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import admin as api_admin
from app.bot.callbacks import UserModerationCallback
from app.bot.handlers import admin as bot_admin
from app.config import Settings
from app.db.models import AppSetting, User, UserStatus


class FakeSession:
    def __init__(self, user: User, setting: AppSetting | None = None) -> None:
        self.user = user
        self.setting = setting
        self.committed = False
        self.rolled_back = False

    async def get(self, model, ident):  # noqa: ANN001
        if model is User and ident == self.user.id:
            return self.user
        return None

    async def scalar(self, _stmt):  # noqa: ANN001
        return self.setting

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeDiskClient:
    def __init__(self, _token: str) -> None:
        self.created = []

    async def mkdir_recursive(self, path: str) -> None:
        self.created.append(path)

    async def close(self) -> None:
        pass


class FakeBot:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


class FakeCallback:
    def __init__(self, admin_id: int = 1) -> None:
        self.from_user = SimpleNamespace(id=admin_id)
        self.answers = []

    async def answer(self, text: str, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


def make_user(status: UserStatus = UserStatus.pending) -> User:
    return User(
        id=10,
        telegram_id=555,
        username="tester",
        full_name="Test User",
        status=status,
        root_folder="disk:/Old/555",
        allowed_folders=["disk:/Old/555"],
    )


@pytest.mark.anyio
async def test_bot_approve_pending_user_uses_runtime_root(monkeypatch) -> None:
    user = make_user(UserStatus.pending)
    session = FakeSession(
        user, AppSetting(key="yandex_disk_root", value="disk:/Runtime", updated_by=1)
    )
    monkeypatch.setattr(bot_admin, "YandexDiskClient", FakeDiskClient)

    async def fake_write_audit(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr(bot_admin, "write_audit", fake_write_audit)

    await bot_admin.user_callback(
        FakeCallback(),
        UserModerationCallback(action="approve", user_id=user.id),
        FakeBot(),
        session,
        Settings(telegram_admin_ids=[1], yandex_disk_root="disk:/Env"),
    )

    assert user.status == UserStatus.active
    assert user.root_folder.startswith("disk:/Runtime/")
    assert user.allowed_folders == [user.root_folder]
    assert session.committed


@pytest.mark.anyio
async def test_bot_repeated_approve_does_not_change_folders(monkeypatch) -> None:
    user = make_user(UserStatus.active)
    session = FakeSession(
        user, AppSetting(key="yandex_disk_root", value="disk:/Runtime", updated_by=1)
    )
    monkeypatch.setattr(bot_admin, "YandexDiskClient", FakeDiskClient)

    async def fake_write_audit(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr(bot_admin, "write_audit", fake_write_audit)
    original_root = user.root_folder
    original_allowed = list(user.allowed_folders)
    callback = FakeCallback()

    await bot_admin.user_callback(
        callback,
        UserModerationCallback(action="approve", user_id=user.id),
        FakeBot(),
        session,
        Settings(telegram_admin_ids=[1], yandex_disk_root="disk:/Env"),
    )

    assert user.root_folder == original_root
    assert user.allowed_folders == original_allowed
    assert not session.committed
    assert callback.answers == [("Пользователь уже обработан: active", True)]


@pytest.mark.anyio
async def test_api_approve_pending_user_uses_runtime_root(monkeypatch) -> None:
    user = make_user(UserStatus.pending)
    session = FakeSession(
        user, AppSetting(key="yandex_disk_root", value="disk:/Runtime", updated_by=1)
    )
    monkeypatch.setattr(api_admin, "YandexDiskClient", FakeDiskClient)

    async def fake_write_audit(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr(api_admin, "write_audit", fake_write_audit)

    result = await api_admin._moderate_user(
        user.id,
        "approve",
        1,
        session,
        Settings(yandex_disk_root="disk:/Env"),
        FakeBot(),
    )

    assert result["status"] == "active"
    assert user.root_folder.startswith("disk:/Runtime/")
    assert user.allowed_folders == [user.root_folder]
    assert session.committed


@pytest.mark.anyio
async def test_api_approve_active_user_returns_400(monkeypatch) -> None:
    user = make_user(UserStatus.active)
    session = FakeSession(
        user, AppSetting(key="yandex_disk_root", value="disk:/Runtime", updated_by=1)
    )
    monkeypatch.setattr(api_admin, "YandexDiskClient", FakeDiskClient)

    async def fake_write_audit(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr(api_admin, "write_audit", fake_write_audit)
    original_root = user.root_folder
    original_allowed = list(user.allowed_folders)

    with pytest.raises(HTTPException) as exc_info:
        await api_admin._moderate_user(
            user.id,
            "approve",
            1,
            session,
            Settings(yandex_disk_root="disk:/Env"),
            FakeBot(),
        )

    assert exc_info.value.status_code == 400
    assert user.root_folder == original_root
    assert user.allowed_folders == original_allowed
    assert not session.committed
