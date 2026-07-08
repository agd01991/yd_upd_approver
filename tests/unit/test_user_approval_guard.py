from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import admin as api_admin
from app.bot.handlers import admin as bot_admin
from app.config import Settings
from app.db.models import UserStatus


class FakeSession:
    def __init__(self, user) -> None:  # noqa: ANN001
        self.user = user
        self.committed = False

    async def get(self, model, obj_id):  # noqa: ANN001, ARG002
        return self.user

    async def scalar(self, statement):  # noqa: ANN001, ARG002
        return None

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.anyio
async def test_api_approve_active_user_returns_400_without_mutating() -> None:
    user = SimpleNamespace(
        id=1,
        telegram_id=10,
        username="u",
        full_name="User",
        status=UserStatus.active,
        root_folder="disk:/Old",
        allowed_folders=["disk:/Old"],
    )
    with pytest.raises(HTTPException) as exc_info:
        await api_admin._moderate_user(1, "approve", 99, FakeSession(user), Settings(), None)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "User is already processed"
    assert user.root_folder == "disk:/Old"
    assert user.allowed_folders == ["disk:/Old"]


@pytest.mark.anyio
async def test_bot_approve_active_user_is_guarded(monkeypatch) -> None:  # noqa: ANN001
    user = SimpleNamespace(
        id=1,
        telegram_id=10,
        username="u",
        full_name="User",
        status=UserStatus.active,
        root_folder="disk:/Old",
        allowed_folders=["disk:/Old"],
    )
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=99),
        answer=AsyncRecorder(),
    )
    monkeypatch.setattr(bot_admin, "ensure_admin_callback", lambda callback, settings: True)
    await bot_admin.user_callback(
        callback,
        SimpleNamespace(action="approve", user_id=1),
        SimpleNamespace(),
        FakeSession(user),
        Settings(telegram_admin_ids=[99]),
    )
    assert callback.answer.calls == [("Пользователь уже обработан", True)]
    assert user.root_folder == "disk:/Old"
    assert user.allowed_folders == ["disk:/Old"]


class AsyncRecorder:
    def __init__(self) -> None:
        self.calls = []

    async def __call__(self, text: str | None = None, show_alert: bool = False) -> None:
        self.calls.append((text, show_alert))
