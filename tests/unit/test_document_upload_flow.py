from pathlib import Path
from types import SimpleNamespace

import pytest

from app.bot.handlers import files
from app.config import Settings
from app.db.models import UserStatus


class FakeMessage:
    def __init__(self, status: UserStatus = UserStatus.active) -> None:
        self.from_user = SimpleNamespace(id=10)
        self.caption = "comment"
        self.document = SimpleNamespace(
            file_id="tg-file",
            file_unique_id="unique",
            file_name="../report.txt",
            mime_type="text/plain",
            file_size=5,
        )
        self.answers = []
        self.status = status

    async def answer(self, text: str, reply_markup=None) -> None:  # noqa: ANN001
        self.answers.append((text, reply_markup))


class FakeBot:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:  # noqa: ANN001
        self.messages.append((chat_id, text, reply_markup))


class FakeSession:
    async def commit(self) -> None:
        self.committed = True


@pytest.mark.anyio
async def test_document_upload_active_user_downloads_and_notifies_admin(
    monkeypatch, tmp_path
) -> None:
    created = {}
    user = SimpleNamespace(
        id=1,
        telegram_id=10,
        username="ivan",
        full_name="Ivan",
        status=UserStatus.active,
        root_folder="disk:/root/10_ivan/",
    )

    async def fake_get_user(session, telegram_id):  # noqa: ANN001
        return user

    async def fake_next_code(session):  # noqa: ANN001
        return "REQ-000001"

    async def fake_download(bot, file_id, destination: Path):  # noqa: ANN001
        destination.write_text("hello")
        return destination

    async def fake_create(session, **kwargs):  # noqa: ANN001
        upload = SimpleNamespace(id=99, status=SimpleNamespace(value="pending_approval"), **kwargs)
        created.update(kwargs)
        return upload

    async def fake_ensure(session, ensured_user, settings, client):  # noqa: ANN001
        return ensured_user.root_folder

    monkeypatch.setattr(files, "get_user_by_tg", fake_get_user)
    monkeypatch.setattr(files, "next_request_code", fake_next_code)
    monkeypatch.setattr(files, "download_file", fake_download)
    monkeypatch.setattr(files, "create_upload_request", fake_create)
    monkeypatch.setattr(files, "ensure_user_folder_for_current_root", fake_ensure)

    message = FakeMessage()
    bot = FakeBot()
    settings = Settings(telegram_admin_ids=[1], temp_storage_dir=tmp_path)
    await files.document_upload(message, bot, FakeSession(), settings)

    assert created["local_path"] != "pending_telegram_download"
    assert Path(created["local_path"]).exists()
    assert created["sha256"] != "0" * 64
    assert message.answers[0][1] is None
    assert "REQ-000001" in message.answers[0][0]
    assert bot.messages == []


@pytest.mark.anyio
@pytest.mark.parametrize("status", [UserStatus.pending, UserStatus.blocked, UserStatus.rejected])
async def test_document_upload_denies_not_active(monkeypatch, tmp_path, status) -> None:  # noqa: ANN001
    async def fake_get_user(session, telegram_id):  # noqa: ANN001
        return SimpleNamespace(status=status)

    monkeypatch.setattr(files, "get_user_by_tg", fake_get_user)
    message = FakeMessage(status=status)
    await files.document_upload(
        message, FakeBot(), FakeSession(), Settings(temp_storage_dir=tmp_path)
    )
    assert message.answers
    assert "REQ-" not in message.answers[0][0]
