import pytest

from app.config import Settings
from app.db.models import AppSetting
from app.services.app_settings import (
    SETTING_YANDEX_DISK_ROOT,
    get_yandex_disk_root,
    get_yandex_disk_root_setting,
    set_yandex_disk_root,
)


class FakeSession:
    def __init__(self, row: AppSetting | None = None) -> None:
        self.row = row
        self.added = None
        self.flushed = False

    async def scalar(self, statement):  # noqa: ANN001
        return self.row

    def add(self, row: AppSetting) -> None:
        self.row = row
        self.added = row

    async def flush(self) -> None:
        self.flushed = True


@pytest.mark.anyio
async def test_get_yandex_disk_root_setting_returns_default() -> None:
    setting = await get_yandex_disk_root_setting(
        FakeSession(), Settings(yandex_disk_root="disk://Default//Root")
    )
    assert setting.value == "disk:/Default/Root"
    assert setting.is_default is True


@pytest.mark.anyio
async def test_get_yandex_disk_root_setting_returns_runtime() -> None:
    session = FakeSession(AppSetting(key=SETTING_YANDEX_DISK_ROOT, value="disk://Runtime"))
    setting = await get_yandex_disk_root_setting(
        session, Settings(yandex_disk_root="disk:/Default")
    )
    assert setting.value == "disk:/Runtime"
    assert setting.is_default is False
    assert (
        await get_yandex_disk_root(session, Settings(yandex_disk_root="disk:/Default"))
        == "disk:/Runtime"
    )


@pytest.mark.anyio
async def test_set_yandex_disk_root_creates_setting() -> None:
    session = FakeSession()
    normalized = await set_yandex_disk_root(session, "disk://Runtime//Root", 42)
    assert normalized == "disk:/Runtime/Root"
    assert session.added is session.row
    assert session.row.key == SETTING_YANDEX_DISK_ROOT
    assert session.row.value == "disk:/Runtime/Root"
    assert session.row.updated_by == 42
    assert session.flushed is True


@pytest.mark.anyio
async def test_set_yandex_disk_root_updates_setting() -> None:
    row = AppSetting(key=SETTING_YANDEX_DISK_ROOT, value="disk:/Old", updated_by=1)
    session = FakeSession(row)
    normalized = await set_yandex_disk_root(session, "disk:/New//Root", 99)
    assert normalized == "disk:/New/Root"
    assert row.value == "disk:/New/Root"
    assert row.updated_by == 99
    assert session.flushed is True
