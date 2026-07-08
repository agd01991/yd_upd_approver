from types import SimpleNamespace

import pytest

from app.bot.handlers import admin
from app.config import Settings
from app.db.models import UploadStatus


class FakeBot:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:  # noqa: ANN001
        self.messages.append((chat_id, text, reply_markup))


class FakeMessage:
    def __init__(self) -> None:
        self.answers = []

    async def answer(self, text: str, reply_markup=None) -> None:  # noqa: ANN001
        self.answers.append((text, reply_markup))


class FakeState:
    async def set_state(self, value) -> None:  # noqa: ANN001
        self.state = value

    async def update_data(self, **kwargs) -> None:  # noqa: ANN003
        self.data = kwargs

    async def get_data(self) -> dict:
        return getattr(self, "data", {})

    async def clear(self) -> None:
        self.cleared = True


class FakeCallback:
    def __init__(self, from_id: int = 1) -> None:
        self.from_user = SimpleNamespace(id=from_id)
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


class FakeSession:
    def __init__(self, request, user) -> None:  # noqa: ANN001
        self.request = request
        self.user = user
        self.committed = False

    async def get(self, model, ident):  # noqa: ANN001
        if ident == self.request.id:
            return self.request
        return self.user

    def add(self, obj) -> None:  # noqa: ANN001
        self.added = obj

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.anyio
async def test_upload_callback_non_admin_denied() -> None:
    callback = FakeCallback(from_id=2)
    await admin.upload_callback(
        callback,
        SimpleNamespace(action="approve", request_id=1),
        FakeBot(),
        FakeSession(SimpleNamespace(id=1), SimpleNamespace(id=2)),
        Settings(telegram_admin_ids=[1]),
        FakeState(),
    )
    assert callback.answers[0] == ("Недостаточно прав", True)


@pytest.mark.anyio
async def test_upload_callback_reject_notifies_user() -> None:
    request = SimpleNamespace(
        id=1,
        user_id=2,
        status=UploadStatus.pending_approval,
        request_code="REQ-000001",
        reject_reason=None,
        rejected_at=None,
    )
    user = SimpleNamespace(id=2, telegram_id=10)
    bot = FakeBot()
    await admin.upload_callback(
        FakeCallback(from_id=1),
        SimpleNamespace(action="reject_duplicate", request_id=1),
        bot,
        FakeSession(request, user),
        Settings(telegram_admin_ids=[1]),
        FakeState(),
    )
    assert request.status == UploadStatus.rejected
    assert request.reject_reason == "Дубликат"
    assert any(chat_id == 10 for chat_id, _, _ in bot.messages)


@pytest.mark.anyio
async def test_upload_callback_approve_uploads_and_notifies(monkeypatch) -> None:  # noqa: ANN001
    request = SimpleNamespace(
        id=1,
        user_id=2,
        status=UploadStatus.pending_approval,
        request_code="REQ-000001",
        approved_at=None,
        approved_by=None,
        target_path="disk:/root/file.txt",
    )
    user = SimpleNamespace(id=2, telegram_id=10)
    bot = FakeBot()

    class FakeClient:
        def __init__(self, token: str) -> None:
            self.token = token

        async def close(self) -> None:
            pass

    async def fake_worker(session, upload, client, overwrite=False):  # noqa: ANN001
        upload.status = UploadStatus.uploaded

    monkeypatch.setattr(admin, "YandexDiskClient", FakeClient)
    monkeypatch.setattr(admin, "upload_approved_request", fake_worker)
    await admin.upload_callback(
        FakeCallback(from_id=1),
        SimpleNamespace(action="approve", request_id=1),
        bot,
        FakeSession(request, user),
        Settings(telegram_admin_ids=[1], yandex_disk_token="token"),
        FakeState(),
    )
    assert request.status == UploadStatus.uploaded
    assert request.approved_by == 1
    assert any(chat_id == 10 and "загружен" in text for chat_id, text, _ in bot.messages)


class FakeTextMessage(FakeMessage):
    def __init__(self, text: str, from_id: int = 1) -> None:
        super().__init__()
        self.text = text
        self.from_user = SimpleNamespace(id=from_id)


@pytest.mark.anyio
async def test_rename_fsm_sanitizes_and_updates_target_path() -> None:
    request = SimpleNamespace(
        id=1,
        user_id=2,
        status=UploadStatus.pending_approval,
        request_code="REQ-20260707-ABCDEF12",
        safe_filename="old.txt",
        target_folder="disk:/root/u/",
        target_path="disk:/root/u/old.txt",
        size_bytes=1,
        mime_type="text/plain",
        sha256="a" * 64,
        caption=None,
    )
    user = SimpleNamespace(id=2, telegram_id=10, username="ivan", full_name="Ivan")
    state = FakeState()
    await state.update_data(request_id=1)
    message = FakeTextMessage("../new/name.pdf")
    await admin.rename_upload(
        message,
        state,
        FakeSession(request, user),
        Settings(telegram_admin_ids=[1]),
    )
    assert request.safe_filename == "name.pdf"
    assert request.target_path == "disk:/root/u/name.pdf"
    assert message.answers[0][1] is not None


@pytest.mark.anyio
async def test_folder_selection_only_uses_allowed_folders() -> None:
    request = SimpleNamespace(
        id=1,
        user_id=2,
        status=UploadStatus.pending_approval,
        request_code="REQ-20260707-ABCDEF12",
        safe_filename="file.txt",
        target_folder="disk:/root/u/",
        target_path="disk:/root/u/file.txt",
        size_bytes=1,
        mime_type="text/plain",
        sha256="a" * 64,
        caption=None,
    )
    user = SimpleNamespace(
        id=2,
        telegram_id=10,
        username="ivan",
        full_name="Ivan",
        allowed_folders=["disk:/root/u/", "disk:/root/u/docs/"],
    )
    callback = FakeCallback(from_id=1)
    await admin.upload_callback(
        callback,
        SimpleNamespace(action="folder_1", request_id=1),
        FakeBot(),
        FakeSession(request, user),
        Settings(telegram_admin_ids=[1]),
        FakeState(),
    )
    assert request.target_folder == "disk:/root/u/docs/"
    assert request.target_path == "disk:/root/u/docs/file.txt"
    assert callback.message.answers[0][1] is not None
