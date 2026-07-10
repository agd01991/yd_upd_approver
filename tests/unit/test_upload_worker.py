from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.models import UploadStatus
from app.workers.upload_worker import upload_approved_request


class FakeSession:
    def __init__(self) -> None:
        self.flushes = 0

    async def flush(self) -> None:
        self.flushes += 1


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def mkdir_recursive(self, path: str) -> None:
        self.folder = path

    async def upload_file(self, local_path: str, target_path: str, overwrite: bool = False) -> None:
        self.upload = (local_path, target_path, overwrite)
        if self.fail:
            msg = "boom"
            raise RuntimeError(msg)


def make_request(path: Path):
    return SimpleNamespace(
        status=UploadStatus.approved,
        local_path=str(path),
        target_folder="disk:/root/u/",
        target_path="disk:/root/u/a.txt",
        error_message=None,
        uploaded_at=None,
    )


@pytest.mark.anyio
async def test_upload_worker_success_deletes_temp(tmp_path) -> None:
    file_path = tmp_path / "a.txt"
    file_path.write_text("hello")
    request = make_request(file_path)
    await upload_approved_request(FakeSession(), request, FakeClient())
    assert request.status == UploadStatus.uploaded
    assert not file_path.exists()


@pytest.mark.anyio
async def test_upload_worker_failed_keeps_temp(tmp_path) -> None:
    file_path = tmp_path / "a.txt"
    file_path.write_text("hello")
    request = make_request(file_path)
    await upload_approved_request(FakeSession(), request, FakeClient(fail=True))
    assert request.status == UploadStatus.failed
    assert file_path.exists()
    assert request.error_message == "Не удалось загрузить файл. Повторите попытку позже."


@pytest.mark.anyio
async def test_upload_worker_missing_file_fails(tmp_path) -> None:
    request = make_request(tmp_path / "missing.txt")
    await upload_approved_request(FakeSession(), request, FakeClient())
    assert request.status == UploadStatus.failed
    assert "Temporary file not found" in request.error_message
