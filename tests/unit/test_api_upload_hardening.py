from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import HTTPException

from app.api.routes.admin import allowed_folders, patch_upload
from app.api.routes.uploads import upload_json
from app.api.schemas import UploadPatch
from app.api.security import TelegramWebAppUser
from app.db.models import UploadStatus, UserStatus


class ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, upload, user) -> None:
        self.upload = upload
        self.user = user
        self.added = []
        self.committed = False

    async def execute(self, stmt):  # noqa: ANN001
        return ScalarResult(self.upload)

    async def get(self, model, ident):
        if model.__name__ == "UploadRequest" and ident == self.upload.id:
            return self.upload
        if model.__name__ == "User" and ident == self.user.id:
            return self.user
        return None

    def add(self, value) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.committed = True


def make_upload(status=UploadStatus.pending_approval):
    return SimpleNamespace(
        id=10,
        request_code="REQ-20260708-ABCD1234",
        user_id=1,
        original_filename="file.pdf",
        safe_filename="file.pdf",
        size_bytes=12345,
        sha256="abc",
        caption="caption",
        status=status,
        target_folder="disk:/Telegram Uploads/123_user/",
        target_path="disk:/Telegram Uploads/123_user/file.pdf",
        error_message=None,
        reject_reason=None,
        created_at=datetime.now(UTC),
        uploaded_at=None,
    )


def make_user(admin=False):
    return SimpleNamespace(
        id=1,
        telegram_id=42 if not admin else 100,
        username="user",
        full_name="User",
        status=UserStatus.active,
        root_folder="disk:/Telegram Uploads/123_user/",
        allowed_folders=[
            "disk:/Telegram Uploads/123_user/",
            "disk:/Telegram Uploads/123_user/docs/",
        ],
    )


def test_user_upload_json_does_not_expose_target_path() -> None:
    data = upload_json(make_upload())

    assert "target_path" not in data
    assert data["request_code"] == "REQ-20260708-ABCD1234"


@pytest.mark.anyio
async def test_allowed_folders_returns_only_request_user_folders() -> None:
    upload = make_upload()
    user = make_user()
    result = await allowed_folders(upload.id, FakeSession(upload, user))

    assert result == {
        "items": [
            {"path": "disk:/Telegram Uploads/123_user/", "label": "123_user"},
            {"path": "disk:/Telegram Uploads/123_user/docs/", "label": "docs"},
        ]
    }


@pytest.mark.anyio
async def test_patch_upload_accepts_only_allowed_folder() -> None:
    upload = make_upload()
    user = make_user()
    session = FakeSession(upload, user)
    actor = TelegramWebAppUser(telegram_id=100)

    result = await patch_upload(
        upload.id,
        UploadPatch(target_folder="disk:/Telegram Uploads/123_user/docs/"),
        actor,
        session,
    )

    assert result["target_folder"] == "disk:/Telegram Uploads/123_user/docs/"
    assert result["target_path"] == "disk:/Telegram Uploads/123_user/docs/file.pdf"
    assert session.committed


@pytest.mark.anyio
async def test_patch_upload_rejects_disallowed_folder() -> None:
    upload = make_upload()
    user = make_user()
    actor = TelegramWebAppUser(telegram_id=100)

    with pytest.raises(HTTPException) as exc:
        await patch_upload(
            upload.id,
            UploadPatch(target_folder="disk:/Telegram Uploads/other/"),
            actor,
            FakeSession(upload, user),
        )

    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_patch_upload_rejects_uploaded_request() -> None:
    upload = make_upload(UploadStatus.uploaded)
    user = make_user()
    actor = TelegramWebAppUser(telegram_id=100)

    with pytest.raises(HTTPException) as exc:
        await patch_upload(
            upload.id,
            UploadPatch(target_folder="disk:/Telegram Uploads/123_user/docs/"),
            actor,
            FakeSession(upload, user),
        )

    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_non_admin_cannot_pass_admin_dependency() -> None:
    from app.api.deps import admin_user_dep
    from app.config import Settings

    with pytest.raises(HTTPException) as exc:
        await admin_user_dep(
            TelegramWebAppUser(telegram_id=42), Settings(telegram_admin_ids=(100,))
        )

    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_patch_upload_filename_stem_preserves_extension() -> None:
    upload = make_upload()
    upload.safe_filename = "old.txt"
    upload.target_path = "disk:/Telegram Uploads/123_user/old.txt"
    result = await patch_upload(
        upload.id,
        UploadPatch(filename_stem="тест"),
        TelegramWebAppUser(telegram_id=100),
        FakeSession(upload, make_user()),
    )
    assert result["safe_filename"] == "тест.txt"
    assert result["target_path"].endswith("/тест.txt")


@pytest.mark.anyio
async def test_patch_upload_filename_extension_changes_extension_only() -> None:
    upload = make_upload()
    upload.safe_filename = "old.txt"
    result = await patch_upload(
        upload.id,
        UploadPatch(filename_extension="pdf"),
        TelegramWebAppUser(telegram_id=100),
        FakeSession(upload, make_user()),
    )
    assert result["safe_filename"] == "old.pdf"


@pytest.mark.anyio
async def test_patch_upload_rejects_invalid_and_conflicting_filename_fields() -> None:
    upload = make_upload()
    user = make_user()
    actor = TelegramWebAppUser(telegram_id=100)
    with pytest.raises(HTTPException) as exc:
        await patch_upload(
            upload.id, UploadPatch(filename_stem="bad.txt"), actor, FakeSession(upload, user)
        )
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await patch_upload(
            upload.id,
            UploadPatch(filename_stem="x", filename_extension="pdf"),
            actor,
            FakeSession(upload, user),
        )
    assert exc.value.status_code == 400


def test_admin_upload_status_parser_accepts_known_and_all_statuses() -> None:
    from app.api.routes.admin import _parse_upload_status

    assert _parse_upload_status(None) is None
    assert _parse_upload_status("all") is None
    assert _parse_upload_status("pending_approval") == UploadStatus.pending_approval
    assert _parse_upload_status("uploaded") == UploadStatus.uploaded


def test_admin_upload_status_parser_rejects_unknown_status() -> None:
    from app.api.routes.admin import _parse_upload_status

    with pytest.raises(HTTPException) as exc:
        _parse_upload_status("unknown")

    assert exc.value.status_code == 400
    assert "Неизвестный статус" in exc.value.detail
