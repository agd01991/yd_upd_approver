from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("aiogram")
from fastapi import HTTPException

from app.api.routes.admin import _moderate_user
from app.bot.handlers.admin import user_callback
from app.config import Settings
from app.db.models import User, UserStatus


class FakeSession:
    def __init__(self, user: User) -> None:
        self.user = user
        self.committed = False
        self.rolled_back = False
        self.added = []

    async def get(self, model, ident):
        return self.user if model is User and ident == self.user.id else None

    def add(self, item) -> None:
        self.added.append(item)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeBot:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


class FakeCallback:
    def __init__(self, admin_id: int) -> None:
        self.from_user = SimpleNamespace(id=admin_id)
        self.answers = []

    async def answer(self, text: str, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


@pytest.mark.anyio
async def test_bot_approve_active_user_does_not_reapprove_or_change_folders() -> None:
    user = User(
        id=1,
        telegram_id=100,
        username="active",
        full_name="Active User",
        status=UserStatus.active,
        root_folder="disk:/Old/100",
        allowed_folders=["disk:/Old/100", "disk:/Old/100/docs"],
    )
    session = FakeSession(user)
    callback = FakeCallback(admin_id=42)
    bot = FakeBot()

    await user_callback(
        callback,
        SimpleNamespace(action="approve", user_id=1),
        bot,
        session,
        Settings(telegram_admin_ids=[42], yandex_disk_root="disk:/NewRoot"),
    )

    assert user.status == UserStatus.active
    assert user.root_folder == "disk:/Old/100"
    assert user.allowed_folders == ["disk:/Old/100", "disk:/Old/100/docs"]
    assert callback.answers == [("Пользователь уже обработан: active", True)]
    assert not session.committed
    assert not session.added
    assert bot.messages == []


@pytest.mark.anyio
async def test_api_approve_active_user_returns_400_and_keeps_existing_folder() -> None:
    user = User(
        id=1,
        telegram_id=100,
        username="active",
        full_name="Active User",
        status=UserStatus.active,
        root_folder="disk:/Old/100",
        allowed_folders=["disk:/Old/100", "disk:/Old/100/docs"],
    )
    session = FakeSession(user)

    with pytest.raises(HTTPException) as exc:
        await _moderate_user(
            1,
            "approve",
            42,
            session,
            Settings(yandex_disk_root="disk:/NewRoot"),
            FakeBot(),
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "User is already processed"
    assert user.root_folder == "disk:/Old/100"
    assert user.allowed_folders == ["disk:/Old/100", "disk:/Old/100/docs"]
    assert not session.committed


@pytest.mark.anyio
async def test_api_approve_pending_user_uses_runtime_root(monkeypatch: pytest.MonkeyPatch) -> None:
    user = User(
        id=1,
        telegram_id=100,
        username="pending",
        full_name="Pending User",
        status=UserStatus.pending,
        allowed_folders=[],
    )
    session = FakeSession(user)
    made_dirs = []

    class FakeClient:
        def __init__(self, token: str) -> None:
            self.token = token

        async def mkdir_recursive(self, path: str) -> None:
            made_dirs.append(path)

        async def close(self) -> None:
            pass

    async def runtime_root(_session, _settings):
        return "disk:/RuntimeRoot"

    monkeypatch.setattr("app.api.routes.admin.YandexDiskClient", FakeClient)
    monkeypatch.setattr("app.api.routes.admin.get_yandex_disk_root", runtime_root)

    result = await _moderate_user(
        1,
        "approve",
        42,
        session,
        Settings(yandex_disk_root="disk:/FallbackRoot"),
        FakeBot(),
    )

    assert result["status"] == "active"
    assert user.root_folder.startswith("disk:/RuntimeRoot/")
    assert user.allowed_folders == [user.root_folder]
    assert made_dirs == [user.root_folder]
    assert session.committed


@pytest.mark.anyio
async def test_api_approve_pending_user_falls_back_to_env_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(
        id=1,
        telegram_id=100,
        username="pending",
        full_name="Pending User",
        status=UserStatus.pending,
        allowed_folders=[],
    )
    session = FakeSession(user)

    class FakeClient:
        def __init__(self, token: str) -> None:
            pass

        async def mkdir_recursive(self, path: str) -> None:
            pass

        async def close(self) -> None:
            pass

    monkeypatch.setattr("app.api.routes.admin.YandexDiskClient", FakeClient)

    await _moderate_user(
        1,
        "approve",
        42,
        session,
        Settings(yandex_disk_root="disk://FallbackRoot"),
        FakeBot(),
    )

    assert user.root_folder.startswith("disk:/FallbackRoot/")
    assert user.allowed_folders == [user.root_folder]
