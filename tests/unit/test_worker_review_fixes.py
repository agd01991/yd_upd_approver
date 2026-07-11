from types import SimpleNamespace

import pytest

from app.bot.handlers import admin
from app.config import Settings
from app.db.models import UploadStatus
from app.services.upload_queue import UploadQueueError, change_upload_folder


class ScalarResult:
    def __init__(self, value) -> None:  # noqa: ANN001
        self.value = value

    def scalar_one_or_none(self):  # noqa: ANN001
        return self.value


class FakeLockedSession:
    def __init__(self, request, user) -> None:  # noqa: ANN001
        self.request = request
        self.user = user
        self.committed = False
        self.added = []
        self.executed = False

    async def execute(self, stmt):  # noqa: ANN001
        self.executed = True
        return ScalarResult(self.request)

    async def get(self, model, ident):  # noqa: ANN001
        if getattr(model, "__name__", "") == "UploadRequest":
            return self.request if ident == self.request.id else None
        if getattr(model, "__name__", "") == "User":
            return self.user if ident == self.user.id else None
        return None

    def add(self, obj) -> None:  # noqa: ANN001
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


def make_request(status: UploadStatus) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        user_id=2,
        status=status,
        request_code="REQ-20260707-ABCDEF12",
        safe_filename="file.txt",
        target_folder="disk:/root/u/",
        target_path="disk:/root/u/file.txt",
        size_bytes=1,
        mime_type="text/plain",
        sha256="a" * 64,
        caption=None,
    )


def make_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=2,
        telegram_id=10,
        username="ivan",
        full_name="Ivan",
        root_folder="disk:/root/u/",
        allowed_folders=["disk:/root/u/", "disk:/root/u/docs/"],
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status",
    [UploadStatus.approved, UploadStatus.uploading, UploadStatus.uploaded, UploadStatus.rejected],
)
async def test_change_upload_folder_rejects_queued_and_final_states(status: UploadStatus) -> None:
    request = make_request(status)
    session = FakeLockedSession(request, make_user())

    with pytest.raises(UploadQueueError):
        await change_upload_folder(session, request.id, "disk:/root/u/docs/", 1)

    assert session.executed
    assert request.target_folder == "disk:/root/u/"
    assert request.target_path == "disk:/root/u/file.txt"
    assert session.added == []
    assert not session.committed


@pytest.mark.anyio
async def test_change_upload_folder_allowed_state_rechecks_allowed_folders() -> None:
    request = make_request(UploadStatus.pending_approval)
    session = FakeLockedSession(request, make_user())

    await change_upload_folder(session, request.id, "disk:/root/u/docs/", 1)

    assert request.target_folder == "disk:/root/u/docs/"
    assert request.target_path == "disk:/root/u/docs/file.txt"
    assert session.added
    assert session.committed


@pytest.mark.anyio
async def test_change_upload_folder_rejects_folder_removed_from_allowed_folders() -> None:
    request = make_request(UploadStatus.pending_approval)
    user = make_user()
    user.allowed_folders = ["disk:/root/u/"]
    session = FakeLockedSession(request, user)

    with pytest.raises(UploadQueueError) as exc:
        await change_upload_folder(session, request.id, "disk:/root/u/docs/", 1)

    assert exc.value.code == "folder_not_allowed"
    assert request.target_folder == "disk:/root/u/"
    assert session.added == []


class FakeCallback:
    def __init__(self) -> None:
        self.from_user = SimpleNamespace(id=1)
        self.answers = []
        self.message = SimpleNamespace(answer=self.message_answer)
        self.message_answers = []

    async def answer(self, text=None, show_alert=False) -> None:  # noqa: ANN001
        self.answers.append((text, show_alert))

    async def message_answer(self, text, reply_markup=None) -> None:  # noqa: ANN001
        self.message_answers.append((text, reply_markup))


@pytest.mark.anyio
async def test_old_folder_callback_for_approved_request_does_not_change_or_audit() -> None:
    request = make_request(UploadStatus.approved)
    session = FakeLockedSession(request, make_user())
    callback = FakeCallback()

    await admin.upload_callback(
        callback,
        SimpleNamespace(action="folder_1", request_id=1),
        SimpleNamespace(),
        session,
        Settings(telegram_admin_ids=[1]),
        SimpleNamespace(),
    )

    assert request.target_folder == "disk:/root/u/"
    assert session.added == []
    assert callback.answers[-1][1] is True
