import pytest
from fastapi import HTTPException

from app.api.routes.admin import admin_folder_rename_requests

pytestmark = pytest.mark.anyio


async def test_folder_rename_requests_bad_status_returns_400() -> None:
    with pytest.raises(HTTPException) as exc:
        await admin_folder_rename_requests(status="bad", session=None)
    assert exc.value.status_code == 400
    assert exc.value.detail == "Неизвестный статус заявки"


def _admin_user_route_index(path: str, method: str) -> int:
    from app.api.routes.admin import router

    for index, route in enumerate(router.routes):
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return index
    raise AssertionError(f"Route {method} {path} is not registered")


def test_specific_user_folder_routes_are_registered_before_moderation_route() -> None:
    moderation_index = _admin_user_route_index("/admin/users/{user_id}/{action}", "POST")

    assert _admin_user_route_index("/admin/users/search", "GET") < moderation_index
    assert (
        _admin_user_route_index("/admin/users/{user_id}/folder-candidates", "GET")
        < moderation_index
    )
    assert (
        _admin_user_route_index("/admin/users/{user_id}/rename-folder", "POST") < moderation_index
    )


def _matched_admin_endpoint(path: str, method: str):
    from starlette.routing import Match

    from app.api.routes.admin import router

    scope = {"type": "http", "path": path, "method": method}
    for route in router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return route.endpoint
    raise AssertionError(f"No route matched {method} {path}")


def test_rename_folder_and_moderation_paths_match_expected_endpoints() -> None:
    from app.api.routes.admin import admin_rename_folder, moderate_user

    assert _matched_admin_endpoint("/admin/users/2/rename-folder", "POST") is admin_rename_folder
    for action in ("approve", "reject", "block"):
        assert _matched_admin_endpoint(f"/admin/users/2/{action}", "POST") is moderate_user


async def test_admin_rename_folder_calls_rename_flow(monkeypatch) -> None:
    from app.api.routes import admin
    from app.api.schemas import AdminRenameFolderBody
    from app.api.security import TelegramWebAppUser
    from app.config import Settings
    from app.db.models import User, UserStatus
    from app.services import user_folder_rename

    user = User(
        id=2,
        telegram_id=200,
        username="user",
        full_name="Test User",
        status=UserStatus.active,
        root_folder="/root/old",
        folder_name="old",
        allowed_folders=["/root/old"],
    )
    calls = {}

    class FakeSession:
        async def get(self, model, item_id):
            calls["session_get"] = (model, item_id)
            return user

        async def commit(self):
            calls["commit"] = True

        async def rollback(self):  # pragma: no cover - should not be called in this test
            calls["rollback"] = True

    class FakeClient:
        def __init__(self, token):
            calls["token"] = token

        async def close(self):
            calls["close"] = True

    async def fake_rename_user_folder(
        session, passed_user, source_folder, new_folder_name, actor, client
    ):
        calls["rename"] = (session, passed_user, source_folder, new_folder_name, actor, client)
        passed_user.folder_name = new_folder_name
        passed_user.root_folder = "/root/new"
        passed_user.allowed_folders = ["/root/new"]
        return "/root/new"

    monkeypatch.setattr(admin, "YandexDiskClient", FakeClient)
    monkeypatch.setattr(user_folder_rename, "rename_user_folder", fake_rename_user_folder)

    result = await admin.admin_rename_folder(
        2,
        AdminRenameFolderBody(source_folder="/root/old", new_folder_name="new"),
        TelegramWebAppUser(telegram_id=99),
        FakeSession(),
        Settings(yandex_disk_token="token"),
    )

    assert calls["session_get"] == (User, 2)
    assert calls["rename"][2:5] == ("/root/old", "new", 99)
    assert calls["commit"] is True
    assert calls["close"] is True
    assert result["target_folder"] == "/root/new"
    assert result["user"]["folder_name"] == "new"


async def test_unknown_moderation_action_still_returns_404() -> None:
    from app.api.routes.admin import moderate_user
    from app.api.security import TelegramWebAppUser
    from app.config import Settings

    with pytest.raises(HTTPException) as exc:
        await moderate_user(
            2,
            "rename-folder",
            TelegramWebAppUser(telegram_id=99),
            session=None,
            settings=Settings(),
            bot=None,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Unknown action"


def test_user_moderation_actions_keep_existing_static_contract() -> None:
    js = __import__("pathlib").Path("app/webapp/static/app.js").read_text()

    assert '["approve", "reject", "block"]' in js
    assert "`/api/admin/users/${id}/${action}`" in js


def test_admin_user_json_exposes_root_folder_label_for_legacy_user() -> None:
    from app.api.routes.admin import user_json
    from app.db.models import User, UserStatus

    data = user_json(
        User(
            id=2,
            telegram_id=200,
            username="user",
            full_name="Test User",
            status=UserStatus.active,
            root_folder="disk:/Root/Legacy Folder/",
            folder_name=None,
        )
    )

    assert data["root_folder_assigned"] is True
    assert data["root_folder_label"] == "Legacy Folder"
    assert data["folder_name"] is None
    assert "yandex_disk_token" not in data
    assert "telegram_bot_token" not in data
