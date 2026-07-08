from types import SimpleNamespace

import pytest

from app.config import Settings
from app.db.models import AppSetting, UserStatus
from app.db.repositories import approve_user
from app.services.app_settings import get_yandex_disk_root, set_yandex_disk_root
from app.services.disk_paths import DiskPathValidationError, validate_yandex_disk_root
from app.services.naming import user_folder


@pytest.mark.parametrize(
    "path",
    ["disk:/Telegram Uploads", "disk:/Загрузки пользователей", "disk:/Telegram Uploads/Test"],
)
def test_validate_yandex_disk_root_valid(path: str) -> None:
    assert validate_yandex_disk_root(path + "/") == path


@pytest.mark.parametrize(
    "path",
    [
        "",
        "../secret",
        "disk:/../secret",
        "disk:/Telegram Uploads/../secret",
        "https://example.com",
        r"C:\Users\test",
        "disk:",
    ],
)
def test_validate_yandex_disk_root_invalid(path: str) -> None:
    with pytest.raises(DiskPathValidationError):
        validate_yandex_disk_root(path)


class FakeSession:
    def __init__(self, setting=None) -> None:  # noqa: ANN001
        self.setting = setting
        self.added = []
        self.flushes = 0

    async def scalar(self, statement):  # noqa: ANN001
        return self.setting

    def add(self, row) -> None:  # noqa: ANN001
        self.added.append(row)
        self.setting = row

    async def flush(self) -> None:
        self.flushes += 1


@pytest.mark.anyio
async def test_get_yandex_disk_root_uses_fallback_without_db_setting() -> None:
    root = await get_yandex_disk_root(
        FakeSession(), Settings(yandex_disk_root="disk:/Fallback Root")
    )
    assert root == "disk:/Fallback Root"


@pytest.mark.anyio
async def test_get_yandex_disk_root_uses_db_setting() -> None:
    root = await get_yandex_disk_root(
        FakeSession(AppSetting(key="yandex_disk_root", value="disk:/Runtime Root")),
        Settings(yandex_disk_root="disk:/Fallback Root"),
    )
    assert root == "disk:/Runtime Root"


@pytest.mark.anyio
async def test_set_yandex_disk_root_creates_and_updates_single_setting() -> None:
    session = FakeSession()
    saved = await set_yandex_disk_root(session, "disk:/New Root/", 100)
    assert saved == "disk:/New Root"
    assert session.setting.value == "disk:/New Root"
    assert session.setting.updated_by == 100
    assert len(session.added) == 1

    saved = await set_yandex_disk_root(session, "disk:/Other Root", 101)
    assert saved == "disk:/Other Root"
    assert session.setting.value == "disk:/Other Root"
    assert session.setting.updated_by == 101
    assert len(session.added) == 1


@pytest.mark.anyio
async def test_approve_user_assigns_runtime_folder_and_existing_users_unchanged() -> None:
    root = "disk:/Runtime Root"
    user = SimpleNamespace(
        telegram_id=42,
        username="ivan",
        full_name="Ivan",
        status=UserStatus.pending,
        root_folder=None,
        allowed_folders=[],
        approved_at=None,
        approved_by=None,
    )
    existing = SimpleNamespace(root_folder="disk:/Old/1_old/", allowed_folders=["disk:/Old/1_old/"])
    await approve_user(FakeSession(), user, 100, root)
    expected = user_folder(root, 42, "Ivan", "ivan")
    assert user.root_folder == expected
    assert user.allowed_folders == [expected]
    assert existing.root_folder == "disk:/Old/1_old/"
    assert existing.allowed_folders == ["disk:/Old/1_old/"]
