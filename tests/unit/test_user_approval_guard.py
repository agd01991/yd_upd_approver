from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import admin as admin_routes
from app.config import Settings
from app.db.models import User, UserStatus


class FakeSession:
    def __init__(self, user) -> None:
        self.user = user
        self.committed = False

    async def get(self, model, ident):  # noqa: ANN001
        if model is User and ident == self.user.id:
            return self.user
        return None

    async def scalar(self, statement):  # noqa: ANN001
        msg = "runtime root lookup should not run for already processed users"
        raise AssertionError(msg)

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.anyio
async def test_api_approve_active_user_returns_400_without_mutating_folders() -> None:
    user = SimpleNamespace(
        id=1,
        telegram_id=42,
        username="user",
        full_name="User",
        status=UserStatus.active,
        root_folder="disk:/Old/42_user/",
        allowed_folders=["disk:/Old/42_user/"],
    )
    session = FakeSession(user)

    with pytest.raises(HTTPException) as exc:
        await admin_routes._moderate_user(
            user.id,
            "approve",
            100,
            session,
            Settings(yandex_disk_root="disk:/New"),
            bot=None,
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "User is already processed"
    assert user.root_folder == "disk:/Old/42_user/"
    assert user.allowed_folders == ["disk:/Old/42_user/"]
    assert session.committed is False
