from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.db.models import UploadMode, UploadStatus
from app.workers import upload_worker
from app.workers.upload_worker import UploadJob, upload_approved_request


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


class FakeWorkerClient:
    instances = []

    def __init__(self, token: str) -> None:
        self.token = token
        self.info_paths = []
        self.uploads = []
        FakeWorkerClient.instances.append(self)

    async def get_info(self, target_path: str) -> dict:
        self.info_paths.append(target_path)
        raise FileNotFoundError(target_path)

    async def mkdir_recursive(self, path: str) -> None:
        self.folder = path

    async def upload_file(self, local_path: str, target_path: str, overwrite: bool = False) -> None:
        self.uploads.append((local_path, target_path, overwrite))

    async def close(self) -> None:
        self.closed = True


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


@pytest.mark.anyio
async def test_process_job_copy_uses_persisted_target_path(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    persisted_target = "disk:/root/u/persisted-before-midnight-copy.txt"
    file_path = tmp_path / "a.txt"
    file_path.write_text("hello")
    finalized = {}

    async def fake_finalize(job: UploadJob, target_path: str) -> bool:
        finalized["target_path"] = target_path
        return True

    async def fake_notify(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None

    async def noop_heartbeat(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr(upload_worker, "YandexDiskClient", FakeWorkerClient)
    monkeypatch.setattr(upload_worker, "finalize_success", fake_finalize)
    monkeypatch.setattr(upload_worker, "notify_result", fake_notify)
    monkeypatch.setattr(upload_worker, "heartbeat", noop_heartbeat)
    monkeypatch.setattr(upload_worker.TempStorage, "delete", lambda path: None)
    FakeWorkerClient.instances.clear()

    job = UploadJob(
        id=1,
        request_code="REQ-000001",
        user_id=2,
        admin_id=3,
        local_path=str(file_path),
        target_folder="disk:/root/u/",
        target_path=persisted_target,
        safe_filename="a.txt",
        size_bytes=5,
        sha256="abc",
        upload_mode=UploadMode.copy,
        worker_token="token",
    )
    settings = Settings(yandex_disk_token="token", temp_storage_dir=tmp_path)

    await upload_worker.process_job(job, settings)

    client = FakeWorkerClient.instances[0]
    assert client.info_paths == [persisted_target]
    assert client.uploads == [(str(file_path), persisted_target, False)]
    assert finalized["target_path"] == persisted_target
