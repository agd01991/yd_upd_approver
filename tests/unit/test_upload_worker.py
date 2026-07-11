import asyncio
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

    async def noop_heartbeat(job, settings, stop) -> None:  # noqa: ANN001
        await stop.wait()

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


class FailingSecretClient(FakeWorkerClient):
    async def upload_file(self, local_path: str, target_path: str, overwrite: bool = False) -> None:
        raise RuntimeError("boom https://upload.example.test/path?token=SUPER_SECRET raw-secret")


@pytest.mark.anyio
async def test_process_job_redacts_upload_exception_from_logs(
    monkeypatch, tmp_path, caplog
) -> None:  # noqa: ANN001
    file_path = tmp_path / "a.txt"
    file_path.write_text("hello")
    finalized = {}

    async def fake_finalize_failure(job: UploadJob, message: str) -> bool:
        finalized["message"] = message
        return True

    async def fake_notify(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None

    async def noop_heartbeat(job, settings, stop) -> None:  # noqa: ANN001
        await stop.wait()

    monkeypatch.setattr(upload_worker, "YandexDiskClient", FailingSecretClient)
    monkeypatch.setattr(upload_worker, "finalize_failure", fake_finalize_failure)
    monkeypatch.setattr(upload_worker, "notify_result", fake_notify)
    monkeypatch.setattr(upload_worker, "heartbeat", noop_heartbeat)
    job = UploadJob(
        1,
        "REQ-000001",
        2,
        3,
        str(file_path),
        "disk:/root/u/",
        "disk:/root/u/a.txt",
        "a.txt",
        5,
        "abc",
        UploadMode.normal,
        "token",
    )

    with caplog.at_level("WARNING", logger="app.workers.upload_worker"):
        await upload_worker.process_job(
            job, Settings(yandex_disk_token="token", temp_storage_dir=tmp_path)
        )

    log_text = caplog.text
    assert finalized["message"] == "Не удалось загрузить файл. Повторите попытку позже."
    assert "RuntimeError" in log_text
    assert "SUPER_SECRET" not in log_text
    assert "https://upload.example.test/path?token=SUPER_SECRET" not in log_text
    assert "raw-secret" not in log_text


class FakeNotifySession:
    def __init__(self, user=None, fail_get: bool = False) -> None:  # noqa: ANN001
        self.user = user
        self.fail_get = fail_get

    async def __aenter__(self):  # noqa: ANN001
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    async def get(self, model, ident):  # noqa: ANN001
        if self.fail_get:
            raise RuntimeError("db https://upload.example.test/path?token=SUPER_SECRET")
        return self.user


class FakeNotifyBot:
    def __init__(self, fail_chats=()) -> None:  # noqa: ANN001
        self.fail_chats = set(fail_chats)
        self.messages = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:  # noqa: ANN001
        self.messages.append((chat_id, text, reply_markup))
        if chat_id in self.fail_chats:
            raise RuntimeError("telegram https://upload.example.test/path?token=SUPER_SECRET")


def make_job(tmp_path):  # noqa: ANN001
    return UploadJob(
        1,
        "REQ-000001",
        2,
        3,
        str(tmp_path / "a.txt"),
        "disk:/root/u/",
        "disk:/root/u/a.txt",
        "a.txt",
        5,
        "abc",
        UploadMode.normal,
        "token",
    )


@pytest.mark.anyio
async def test_notify_result_user_failure_does_not_block_admin(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        upload_worker, "SessionLocal", lambda: FakeNotifySession(SimpleNamespace(telegram_id=2))
    )
    bot = FakeNotifyBot(fail_chats={2})
    await upload_worker.notify_result(bot, make_job(tmp_path), UploadStatus.uploaded)
    assert [chat_id for chat_id, *_ in bot.messages] == [3, 2]


@pytest.mark.anyio
async def test_notify_result_admin_failure_does_not_block_user(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        upload_worker, "SessionLocal", lambda: FakeNotifySession(SimpleNamespace(telegram_id=2))
    )
    bot = FakeNotifyBot(fail_chats={3})
    await upload_worker.notify_result(bot, make_job(tmp_path), UploadStatus.failed, "safe failure")
    assert [chat_id for chat_id, *_ in bot.messages] == [3, 2]
    assert all("SUPER_SECRET" not in text for _, text, _ in bot.messages)


@pytest.mark.anyio
async def test_notify_result_all_failures_are_swallowed(monkeypatch, tmp_path, caplog) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        upload_worker, "SessionLocal", lambda: FakeNotifySession(SimpleNamespace(telegram_id=2))
    )
    bot = FakeNotifyBot(fail_chats={2, 3})
    with caplog.at_level("WARNING", logger="app.workers.upload_worker"):
        await upload_worker.notify_result(
            bot, make_job(tmp_path), UploadStatus.failed, "safe failure"
        )
    assert [chat_id for chat_id, *_ in bot.messages] == [3, 2]
    assert "SUPER_SECRET" not in caplog.text


@pytest.mark.anyio
async def test_failed_admin_notification_has_retry_keyboard(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        upload_worker, "SessionLocal", lambda: FakeNotifySession(SimpleNamespace(telegram_id=2))
    )
    bot = FakeNotifyBot()
    await upload_worker.notify_result(bot, make_job(tmp_path), UploadStatus.failed, "safe failure")
    admin_markup = bot.messages[0][2]
    user_markup = bot.messages[1][2]
    callbacks = [row[0].callback_data for row in admin_markup.inline_keyboard]
    assert any(":retry:" in data or "action=retry" in data or "retry" in data for data in callbacks)
    assert any("copy" in data for data in callbacks)
    assert any("overwrite" in data for data in callbacks)
    assert user_markup is None


@pytest.mark.anyio
async def test_success_admin_notification_has_no_retry_keyboard(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        upload_worker, "SessionLocal", lambda: FakeNotifySession(SimpleNamespace(telegram_id=2))
    )
    bot = FakeNotifyBot()
    await upload_worker.notify_result(bot, make_job(tmp_path), UploadStatus.uploaded)
    assert bot.messages[0][2] is None


@pytest.mark.anyio
async def test_heartbeat_failure_cancels_upload_without_failure_finalize(
    monkeypatch, tmp_path
) -> None:  # noqa: ANN001
    file_path = tmp_path / "a.txt"
    file_path.write_text("hello")
    upload_cancelled = asyncio.Event()
    finalized_failure = False

    async def blocking_upload(job, settings):  # noqa: ANN001
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            upload_cancelled.set()
            raise

    async def failing_heartbeat(job, settings, stop):  # noqa: ANN001
        await asyncio.sleep(0)
        raise upload_worker.HeartbeatError("heartbeat failed")

    async def fake_finalize_failure(job, message):  # noqa: ANN001
        nonlocal finalized_failure
        finalized_failure = True
        return True

    monkeypatch.setattr(upload_worker, "_upload_remote", blocking_upload)
    monkeypatch.setattr(upload_worker, "heartbeat", failing_heartbeat)
    monkeypatch.setattr(upload_worker, "finalize_failure", fake_finalize_failure)

    await upload_worker.process_job(make_job(tmp_path), Settings(temp_storage_dir=tmp_path))

    assert upload_cancelled.is_set()
    assert not finalized_failure
    assert file_path.exists()


@pytest.mark.anyio
async def test_lease_lost_cancels_upload_without_failure_finalize(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    upload_cancelled = asyncio.Event()
    finalized_failure = False

    async def blocking_upload(job, settings):  # noqa: ANN001
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            upload_cancelled.set()
            raise

    async def lost_heartbeat(job, settings, stop):  # noqa: ANN001
        raise upload_worker.LeaseLostError("lost")

    async def fake_finalize_failure(job, message):  # noqa: ANN001
        nonlocal finalized_failure
        finalized_failure = True
        return True

    monkeypatch.setattr(upload_worker, "_upload_remote", blocking_upload)
    monkeypatch.setattr(upload_worker, "heartbeat", lost_heartbeat)
    monkeypatch.setattr(upload_worker, "finalize_failure", fake_finalize_failure)

    await upload_worker.process_job(make_job(tmp_path), Settings(temp_storage_dir=tmp_path))

    assert upload_cancelled.is_set()
    assert not finalized_failure


@pytest.mark.anyio
async def test_remote_success_finalize_exception_does_not_mark_failed(
    monkeypatch, tmp_path
) -> None:  # noqa: ANN001
    file_path = tmp_path / "a.txt"
    file_path.write_text("hello")
    failure_called = False
    notified = False

    async def successful_upload(job, settings):  # noqa: ANN001
        return True

    async def broken_finalize_success(job, target_path):  # noqa: ANN001
        raise RuntimeError("db password=secret")

    async def fake_finalize_failure(job, message):  # noqa: ANN001
        nonlocal failure_called
        failure_called = True
        return True

    async def fake_notify(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        nonlocal notified
        notified = True

    monkeypatch.setattr(upload_worker, "_run_upload_with_heartbeat", successful_upload)
    monkeypatch.setattr(upload_worker, "finalize_success", broken_finalize_success)
    monkeypatch.setattr(upload_worker, "finalize_failure", fake_finalize_failure)
    monkeypatch.setattr(upload_worker, "notify_result", fake_notify)

    await upload_worker.process_job(make_job(tmp_path), Settings(temp_storage_dir=tmp_path))

    assert not failure_called
    assert not notified
    assert file_path.exists()


@pytest.mark.anyio
async def test_temp_delete_error_after_success_does_not_mark_failed(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    failure_called = False
    notified = False

    async def successful_upload(job, settings):  # noqa: ANN001
        return True

    async def successful_finalize(job, target_path):  # noqa: ANN001
        return True

    async def fake_finalize_failure(job, message):  # noqa: ANN001
        nonlocal failure_called
        failure_called = True
        return True

    async def fake_notify(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        nonlocal notified
        notified = True

    monkeypatch.setattr(upload_worker, "_run_upload_with_heartbeat", successful_upload)
    monkeypatch.setattr(upload_worker, "finalize_success", successful_finalize)
    monkeypatch.setattr(upload_worker, "finalize_failure", fake_finalize_failure)
    monkeypatch.setattr(
        upload_worker.TempStorage,
        "delete",
        lambda path: (_ for _ in ()).throw(RuntimeError("delete")),
    )
    monkeypatch.setattr(upload_worker, "notify_result", fake_notify)

    await upload_worker.process_job(make_job(tmp_path), Settings(temp_storage_dir=tmp_path))

    assert not failure_called
    assert notified
