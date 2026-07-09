import pytest

from app.config import Settings
from app.db.models import AppSetting
from app.services.app_settings import (
    get_yandex_disk_root,
    get_yandex_disk_root_setting,
    set_yandex_disk_root,
)

pytestmark = pytest.mark.anyio


class FakeSession:
    def __init__(self, row=None):
        self.row = row
        self.added = []
        self.settings_rows = []
        self.flushed = False
        self.committed = False

    async def scalar(self, _stmt):
        return self.row

    def add(self, row):
        if isinstance(row, AppSetting):
            self.row = row
            self.settings_rows.append(row)
        self.added.append(row)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True


async def test_get_yandex_disk_root_setting_uses_default_when_missing() -> None:
    session = FakeSession()
    result = await get_yandex_disk_root_setting(
        session, Settings(yandex_disk_root="disk://Default")
    )
    assert result.value == "disk:/Default"
    assert result.is_default is True


async def test_get_yandex_disk_root_setting_uses_database_value() -> None:
    session = FakeSession(AppSetting(key="yandex_disk_root", value="disk:/DB//Root", updated_by=1))
    result = await get_yandex_disk_root_setting(session, Settings(yandex_disk_root="disk:/Default"))
    assert result.value == "disk:/DB/Root"
    assert result.is_default is False
    assert (
        await get_yandex_disk_root(session, Settings(yandex_disk_root="disk:/Default"))
        == "disk:/DB/Root"
    )


async def test_set_yandex_disk_root_creates_flushes_and_does_not_commit() -> None:
    session = FakeSession()
    value = await set_yandex_disk_root(session, "disk://New", 42)
    assert value == "disk:/New"
    assert session.row.value == "disk:/New"
    assert session.row.updated_by == 42
    assert session.flushed is True
    assert session.committed is False


async def test_set_yandex_disk_root_updates_existing_row() -> None:
    row = AppSetting(key="yandex_disk_root", value="disk:/Old", updated_by=1)
    session = FakeSession(row)
    value = await set_yandex_disk_root(session, "disk:/New/Root/", 99)
    assert value == "disk:/New/Root"
    assert row.value == "disk:/New/Root"
    assert row.updated_by == 99
    assert session.flushed is True
    assert session.added == []
    assert session.committed is False


async def test_setdiskroot_helper_creates_folder_saves_audit_and_commits(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.bot.handlers import admin as admin_handlers

    answers = []
    mkdir_calls = []

    class FakeClient:
        def __init__(self, token):
            self.token = token

        async def mkdir_recursive(self, path):
            mkdir_calls.append(path)

        async def close(self):
            pass

    class FakeMessage:
        from_user = SimpleNamespace(id=123)

        async def answer(self, text):
            answers.append(text)

    session = FakeSession()
    monkeypatch.setattr(admin_handlers, "YandexDiskClient", FakeClient)
    await admin_handlers._save_diskroot_change(
        FakeMessage(), session, Settings(yandex_disk_root="disk:/Old"), "disk://New//Root/"
    )
    assert mkdir_calls == ["disk:/New/Root"]
    assert session.row.value == "disk:/New/Root"
    assert session.committed is True
    assert "Корневая папка Яндекс.Диска обновлена" in answers[0]


async def test_setdiskroot_helper_does_not_save_when_mkdir_fails(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.bot.handlers import admin as admin_handlers

    answers = []

    class FakeClient:
        def __init__(self, token):
            self.token = token

        async def mkdir_recursive(self, path):
            raise RuntimeError("boom")

        async def close(self):
            pass

    class RollbackSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.rolled_back = False

        async def rollback(self):
            self.rolled_back = True

    class FakeMessage:
        from_user = SimpleNamespace(id=123)

        async def answer(self, text):
            answers.append(text)

    session = RollbackSession()
    monkeypatch.setattr(admin_handlers, "YandexDiskClient", FakeClient)
    await admin_handlers._save_diskroot_change(
        FakeMessage(), session, Settings(yandex_disk_root="disk:/Old"), "disk:/New"
    )
    assert session.settings_rows == []
    assert session.committed is False
    assert session.rolled_back is True
    assert "Не удалось создать папку" in answers[0]
