from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.api.routes import uploads
from app.bot.handlers import files
from app.config import Settings
from app.db.models import UserStatus

pytestmark = pytest.mark.anyio


class ExistingSession:
    def __init__(self, existing):
        self.existing = existing
        self.rolled_back = False

    async def scalar(self, stmt):
        return self.existing if self.rolled_back else None

    async def rollback(self):
        self.rolled_back = True

    async def commit(self):
        pass


async def test_mini_app_integrity_error_during_insert_returns_existing_and_deletes_temp(
    monkeypatch, tmp_path
):
    user = SimpleNamespace(id=1, status=UserStatus.active, root_folder="disk:/root/u")
    existing = SimpleNamespace(
        request_code="REQ-OLD", status=SimpleNamespace(value="pending_approval")
    )
    session = ExistingSession(existing)

    async def fake_next(session):
        return "REQ-NEW"

    async def fake_ensure(*args):
        return "disk:/root/u"

    async def fake_create(*args, **kwargs):
        assert Path(kwargs["local_path"]).exists()
        raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(uploads, "next_request_code", fake_next)
    monkeypatch.setattr(uploads, "ensure_user_folder_for_current_root", fake_ensure)
    monkeypatch.setattr(uploads, "create_upload_request", fake_create)
    result = await uploads.create_upload(
        file=SimpleNamespace(
            filename="a.txt", content_type="text/plain", read=AsyncReader([b"x"]).read
        ),
        caption=None,
        current=(user, False),
        session=session,
        settings=Settings(temp_storage_dir=tmp_path),
        idempotency_key="same",
    )
    assert result == {"request_code": "REQ-OLD", "status": "pending_approval"}
    assert not any(tmp_path.rglob("*.*"))


class AsyncReader:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def read(self, _size):
        return self.chunks.pop(0) if self.chunks else b""


class FakeMessage:
    def __init__(self):
        self.from_user = SimpleNamespace(id=10)
        self.chat = SimpleNamespace(id=20)
        self.message_id = 30
        self.caption = None
        self.document = SimpleNamespace(
            file_id="f", file_unique_id="u", file_name="a.txt", mime_type="text/plain", file_size=1
        )
        self.answers = []

    async def answer(self, text, reply_markup=None):
        self.answers.append(text)


async def test_document_integrity_error_during_insert_answers_existing_and_deletes_temp(
    monkeypatch, tmp_path
):
    user = SimpleNamespace(
        id=1, telegram_id=10, status=UserStatus.active, root_folder="disk:/root/u"
    )
    existing = SimpleNamespace(
        request_code="REQ-OLD", status=SimpleNamespace(value="pending_approval")
    )
    session = ExistingSession(existing)

    async def fake_get_user(*args):
        return user

    async def fake_next(*args):
        return "REQ-NEW"

    async def fake_download(bot, file_id, destination):
        destination.write_text("x")

    async def fake_ensure(*args):
        return "disk:/root/u"

    async def fake_create(*args, **kwargs):
        raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(files, "get_user_by_tg", fake_get_user)
    monkeypatch.setattr(files, "next_request_code", fake_next)
    monkeypatch.setattr(files, "download_file", fake_download)
    monkeypatch.setattr(files, "ensure_user_folder_for_current_root", fake_ensure)
    monkeypatch.setattr(files, "create_upload_request", fake_create)
    message = FakeMessage()
    await files.document_upload(
        message, SimpleNamespace(), session, Settings(temp_storage_dir=tmp_path)
    )
    assert message.answers == ["Файл уже получен: REQ-OLD (статус: pending_approval)"]
    assert not any(tmp_path.rglob("*.*"))
