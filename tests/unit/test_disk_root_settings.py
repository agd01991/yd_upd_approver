from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services import app_settings


class FakeSession:
    def __init__(self, setting=None) -> None:  # noqa: ANN001
        self.setting = setting
        self.added = None
        self.flushed = False

    async def scalar(self, statement):  # noqa: ANN001, ARG002
        return self.setting

    def add(self, setting):  # noqa: ANN001
        self.added = setting
        self.setting = setting

    async def flush(self) -> None:
        self.flushed = True


@pytest.mark.anyio
async def test_get_yandex_disk_root_falls_back_to_env_when_db_setting_missing() -> None:
    root = await app_settings.get_yandex_disk_root(
        FakeSession(), Settings(yandex_disk_root="disk://Fallback")
    )
    assert root == "disk:/Fallback"


@pytest.mark.anyio
async def test_get_yandex_disk_root_prefers_db_setting() -> None:
    root = await app_settings.get_yandex_disk_root(
        FakeSession(SimpleNamespace(value="disk:/Runtime//Root")),
        Settings(yandex_disk_root="disk:/Fallback"),
    )
    assert root == "disk:/Runtime/Root"


@pytest.mark.anyio
async def test_set_yandex_disk_root_creates_setting() -> None:
    session = FakeSession()
    setting = await app_settings.set_yandex_disk_root(session, "disk://Runtime", updated_by=123)
    assert session.added is setting
    assert setting.value == "disk:/Runtime"
    assert setting.updated_by == 123
    assert session.flushed


@pytest.mark.anyio
async def test_set_yandex_disk_root_updates_setting() -> None:
    existing = SimpleNamespace(value="disk:/Old", updated_by=None)
    session = FakeSession(existing)
    setting = await app_settings.set_yandex_disk_root(session, "disk:/New//Root", updated_by=456)
    assert setting is existing
    assert setting.value == "disk:/New/Root"
    assert setting.updated_by == 456
    assert session.flushed
