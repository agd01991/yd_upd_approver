import pytest

from app.config import Settings
from app.db.models import AppSetting
from app.services.app_settings import get_yandex_disk_root, set_yandex_disk_root


class FakeSession:
    def __init__(self, setting: AppSetting | None = None) -> None:
        self.setting = setting
        self.added = None
        self.flushes = 0

    async def scalar(self, _stmt):  # noqa: ANN001
        return self.setting

    def add(self, setting: AppSetting) -> None:
        self.setting = setting
        self.added = setting

    async def flush(self) -> None:
        self.flushes += 1


@pytest.mark.anyio
async def test_get_yandex_disk_root_falls_back_to_env() -> None:
    session = FakeSession()
    settings = Settings(yandex_disk_root="disk:/Env Root")

    assert await get_yandex_disk_root(session, settings) == "disk:/Env Root"


@pytest.mark.anyio
async def test_get_yandex_disk_root_uses_db_setting() -> None:
    session = FakeSession(AppSetting(key="yandex_disk_root", value="disk://Runtime", updated_by=1))
    settings = Settings(yandex_disk_root="disk:/Env Root")

    assert await get_yandex_disk_root(session, settings) == "disk:/Runtime"


@pytest.mark.anyio
async def test_set_yandex_disk_root_creates_setting() -> None:
    session = FakeSession()

    setting = await set_yandex_disk_root(session, "disk://New Root", updated_by=42)

    assert setting is session.added
    assert setting.value == "disk:/New Root"
    assert setting.updated_by == 42
    assert session.flushes == 1


@pytest.mark.anyio
async def test_set_yandex_disk_root_updates_existing_setting() -> None:
    existing = AppSetting(key="yandex_disk_root", value="disk:/Old", updated_by=1)
    session = FakeSession(existing)

    setting = await set_yandex_disk_root(session, "disk:/New//Child/", updated_by=42)

    assert setting is existing
    assert setting.value == "disk:/New/Child"
    assert setting.updated_by == 42
    assert session.flushes == 1
