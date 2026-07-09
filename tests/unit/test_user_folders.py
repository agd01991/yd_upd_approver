import pytest

from app.config import Settings
from app.db.models import AppSetting, UploadRequest, User, UserStatus
from app.services.user_folders import (
    change_yandex_disk_root_for_active_users,
    ensure_user_folder_for_current_root,
)

pytestmark = pytest.mark.anyio


class FakeSession:
    def __init__(self, setting=None, users=None):
        self.setting = setting
        self.users = users or []
        self.flushed = 0
        self.added = []

    async def scalar(self, _stmt):
        return self.setting

    async def scalars(self, _stmt):
        class Result:
            def __init__(self, rows):
                self.rows = rows

            def all(self):
                return self.rows

        return Result(self.users)

    def add(self, row):
        if isinstance(row, AppSetting):
            self.setting = row
        self.added.append(row)

    async def flush(self):
        self.flushed += 1


class FakeClient:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    async def mkdir_recursive(self, path):
        self.calls.append(path)
        if self.fail_at == path:
            raise RuntimeError("boom")


def user(**kwargs):
    data = dict(id=1, telegram_id=123, username="user", full_name="User", status=UserStatus.active)
    data.update(kwargs)
    return User(**data)


async def test_ensure_user_folder_keeps_matching_folder_when_profile_changes() -> None:
    u = user(
        username="newname",
        full_name="New Name",
        root_folder="disk:/Root/123_oldname/",
        allowed_folders=["disk:/Root/123_oldname/"],
    )
    session = FakeSession()
    client = FakeClient()
    folder = await ensure_user_folder_for_current_root(
        session, u, Settings(yandex_disk_root="disk:/Root", yandex_disk_token="token"), client
    )
    assert folder == "disk:/Root/123_oldname/"
    assert u.root_folder == "disk:/Root/123_oldname/"
    assert u.allowed_folders == ["disk:/Root/123_oldname/"]
    assert client.calls == ["disk:/Root/123_oldname/"]
    assert session.flushed == 0


async def test_ensure_user_folder_updates_when_root_changed() -> None:
    u = user(
        username="newname",
        full_name="New Name",
        root_folder="disk:/Old/123_oldname/",
        allowed_folders=["disk:/Old/123_oldname/"],
    )
    session = FakeSession()
    client = FakeClient()
    folder = await ensure_user_folder_for_current_root(
        session, u, Settings(yandex_disk_root="disk:/New", yandex_disk_token="token"), client
    )
    assert folder == "disk:/New/123_oldname/"
    assert u.root_folder == "disk:/New/123_oldname/"
    assert u.allowed_folders == ["disk:/New/123_oldname/"]
    assert session.flushed == 1


async def test_ensure_user_folder_creates_new_folder_when_missing() -> None:
    u = user(username="newname", full_name="New Name", root_folder=None, allowed_folders=[])
    session = FakeSession()
    client = FakeClient()
    folder = await ensure_user_folder_for_current_root(
        session, u, Settings(yandex_disk_root="disk:/Root", yandex_disk_token="token"), client
    )
    assert folder == "disk:/Root/123_newname/"
    assert u.root_folder == "disk:/Root/123_newname/"
    assert u.allowed_folders == ["disk:/Root/123_newname/"]
    assert session.flushed == 1


async def test_ensure_user_folder_does_not_update_when_mkdir_fails() -> None:
    u = user(root_folder="disk:/Old/123_user/", allowed_folders=["disk:/Old/123_user/"])
    session = FakeSession()
    client = FakeClient(fail_at="disk:/New/123_user/")
    with pytest.raises(RuntimeError):
        await ensure_user_folder_for_current_root(
            session, u, Settings(yandex_disk_root="disk:/New", yandex_disk_token="token"), client
        )
    assert u.root_folder == "disk:/Old/123_user/"
    assert u.allowed_folders == ["disk:/Old/123_user/"]


async def test_change_root_updates_active_users_with_stable_basename_and_not_upload_requests() -> (
    None
):
    u = user(
        username="newname",
        full_name="New Name",
        root_folder="disk:/Old/123_oldname/",
        allowed_folders=["disk:/Old/123_oldname/"],
    )
    upload = UploadRequest(
        target_folder="disk:/Old/123_oldname/", target_path="disk:/Old/123_oldname/a.txt"
    )
    session = FakeSession(users=[u])
    client = FakeClient()
    root = await change_yandex_disk_root_for_active_users(
        session,
        Settings(yandex_disk_root="disk:/Old", yandex_disk_token="token"),
        client,
        "disk:/New",
        99,
    )
    assert root == "disk:/New"
    assert client.calls == ["disk:/New", "disk:/New/123_oldname/"]
    assert u.root_folder == "disk:/New/123_oldname/"
    assert u.allowed_folders == ["disk:/New/123_oldname/"]
    assert upload.target_folder == "disk:/Old/123_oldname/"
    assert session.setting is not None
    assert session.setting.value == "disk:/New"
    assert any(row.__class__.__name__ == "AuditLog" for row in session.added)


async def test_change_root_does_not_save_setting_when_user_folder_fails() -> None:
    u = user(root_folder="disk:/Old/123_user/", allowed_folders=["disk:/Old/123_user/"])
    session = FakeSession(users=[u])
    client = FakeClient(fail_at="disk:/New/123_user/")
    with pytest.raises(RuntimeError):
        await change_yandex_disk_root_for_active_users(
            session,
            Settings(yandex_disk_root="disk:/Old", yandex_disk_token="token"),
            client,
            "disk:/New",
            99,
        )
    assert session.setting is None
    assert u.root_folder == "disk:/Old/123_user/"


async def test_change_root_same_value_does_not_rewrite_users_or_audit() -> None:
    u = user(
        username="newname",
        full_name="New Name",
        root_folder="disk:/Old/123_oldname/",
        allowed_folders=["disk:/Old/123_oldname/"],
    )
    session = FakeSession(users=[u])
    client = FakeClient()
    root = await change_yandex_disk_root_for_active_users(
        session,
        Settings(yandex_disk_root="disk:/Old", yandex_disk_token="token"),
        client,
        "disk:/Old/",
        99,
    )
    assert root == "disk:/Old"
    assert client.calls == ["disk:/Old"]
    assert u.root_folder == "disk:/Old/123_oldname/"
    assert u.allowed_folders == ["disk:/Old/123_oldname/"]
    assert session.setting is None
    assert session.added == []
    assert session.flushed == 0
