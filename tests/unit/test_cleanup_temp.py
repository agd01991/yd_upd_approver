from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.db.models import TelegramOutboxStatus, UploadStatus
from app.scripts import cleanup_temp


class FakeScalars:
    def __init__(self, rows) -> None:  # noqa: ANN001
        self.rows = rows

    def all(self):  # noqa: ANN201
        return self.rows


class FakeSession:
    def __init__(self, rows) -> None:  # noqa: ANN001
        self.rows = rows
        self.committed = False

    async def __aenter__(self):  # noqa: ANN201
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    async def scalar(self, statement):  # noqa: ANN001
        text = str(statement)
        if "max" in text.lower():
            ids = [getattr(row, "id", None) for row in self.rows]
            ids = [row_id for row_id in ids if row_id is not None]
            return max(ids, default=None)
        return False

    async def scalars(self, statement):  # noqa: ANN001
        return FakeScalars(self.rows)

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.anyio
async def test_cleanup_deletes_only_terminal_status_temp_files(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    old_file = tmp_path / "old.txt"
    old_file.write_text("old")
    failed_file = tmp_path / "failed.txt"
    failed_file.write_text("failed")
    rows = [
        SimpleNamespace(
            id=1,
            status=UploadStatus.uploaded,
            local_path=str(old_file),
            created_at=datetime.now(UTC) - timedelta(days=10),
        ),
        SimpleNamespace(
            id=2,
            status=UploadStatus.failed,
            local_path=str(failed_file),
            created_at=datetime.now(UTC) - timedelta(days=10),
        ),
    ]
    monkeypatch.setattr(cleanup_temp, "SessionLocal", lambda: FakeSession(rows))
    monkeypatch.setattr(
        cleanup_temp, "get_settings", lambda: SimpleNamespace(rejected_retention_days=7)
    )
    deleted = await cleanup_temp.cleanup_temp()
    assert deleted == 1
    assert rows[0].status == UploadStatus.deleted_temp
    assert not old_file.exists()
    assert failed_file.exists()


@pytest.mark.anyio
async def test_cleanup_keyset_pagination_does_not_starve_after_unsafe_batch(
    monkeypatch, tmp_path
) -> None:  # noqa: ANN001
    batch_size = 2
    external = tmp_path / "external"
    external.mkdir()
    safe_root = tmp_path / "safe"
    safe_root.mkdir()
    safe_file = safe_root / "REQ-SAFE" / "ok.txt"
    safe_file.parent.mkdir()
    safe_file.write_text("ok")
    rows = [
        SimpleNamespace(
            id=1,
            status=UploadStatus.uploaded,
            local_path=str(external / "1.txt"),
            created_at=datetime.now(UTC),
        ),
        SimpleNamespace(
            id=2,
            status=UploadStatus.uploaded,
            local_path=str(external / "2.txt"),
            created_at=datetime.now(UTC),
        ),
        SimpleNamespace(
            id=3,
            status=UploadStatus.uploaded,
            local_path=str(safe_file),
            created_at=datetime.now(UTC),
        ),
    ]

    class PagingSession(FakeSession):
        async def scalars(self, statement):  # noqa: ANN001
            text = str(statement.compile(compile_kwargs={"literal_binds": True}))
            if "upload_requests.id > 0" in text:
                return FakeScalars(rows[:batch_size])
            return FakeScalars(rows[batch_size:])

    monkeypatch.setattr(cleanup_temp, "SessionLocal", lambda: PagingSession(rows))
    monkeypatch.setattr(
        cleanup_temp,
        "get_settings",
        lambda: SimpleNamespace(
            rejected_retention_days=7,
            temp_storage_dir=safe_root,
            temp_cleanup_batch_size=batch_size,
            temp_part_retention_seconds=3600,
        ),
    )

    async def no_outbox(*_):  # noqa: ANN002
        return False

    monkeypatch.setattr(cleanup_temp, "_has_pending_terminal_upload_notification", no_outbox)

    deleted = await cleanup_temp.cleanup_temp()

    assert deleted == 1
    assert not safe_file.exists()
    assert rows[2].status == UploadStatus.deleted_temp
    assert rows[0].status == UploadStatus.uploaded
    assert rows[1].status == UploadStatus.uploaded


@pytest.mark.anyio
async def test_cleanup_keeps_status_while_upload_result_outbox_pending(
    monkeypatch, tmp_path
) -> None:  # noqa: ANN001
    path = tmp_path / "REQ" / "file.txt"
    path.parent.mkdir()
    path.write_text("ok")
    row = SimpleNamespace(
        id=10, status=UploadStatus.uploaded, local_path=str(path), created_at=datetime.now(UTC)
    )
    monkeypatch.setattr(cleanup_temp, "SessionLocal", lambda: FakeSession([row]))
    monkeypatch.setattr(
        cleanup_temp,
        "get_settings",
        lambda: SimpleNamespace(
            rejected_retention_days=7,
            temp_storage_dir=tmp_path,
            temp_cleanup_batch_size=100,
            temp_part_retention_seconds=3600,
        ),
    )

    async def pending(*_):  # noqa: ANN002
        return True

    monkeypatch.setattr(cleanup_temp, "_has_pending_terminal_upload_notification", pending)

    assert await cleanup_temp.cleanup_temp() == 1
    assert row.status == UploadStatus.uploaded
    assert not path.exists()


@pytest.mark.anyio
async def test_cleanup_scan_cycle_high_water_prevents_sustained_load_starvation(
    monkeypatch, tmp_path
) -> None:  # noqa: ANN001
    batch_size = 2
    now = datetime.now(UTC)
    safe_root = tmp_path / "safe"
    safe_root.mkdir()
    rows = []
    checked_batches = []

    def add_row(row_id: int, *, eligible: bool = True):
        path = safe_root / f"REQ-{row_id}" / "file.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(row_id))
        row = SimpleNamespace(
            id=row_id,
            status=UploadStatus.uploaded if eligible else UploadStatus.failed,
            local_path=str(path),
            created_at=now,
        )
        rows.append(row)
        rows.sort(key=lambda item: item.id)
        return row

    add_row(1)
    late_low = add_row(2, eligible=False)
    add_row(3)
    add_row(4)
    add_row(5)

    class HighWaterSession(FakeSession):
        async def scalars(self, statement):  # noqa: ANN001
            text = str(statement.compile(compile_kwargs={"literal_binds": True}))
            lower = text.lower()
            after = 0
            high_water = max(row.id for row in rows)
            if "upload_requests.id > " in lower:
                after = int(lower.split("upload_requests.id > ", 1)[1].split()[0])
            if "upload_requests.id <= " in lower:
                high_water = int(lower.split("upload_requests.id <= ", 1)[1].split()[0])
            selected = [
                row
                for row in rows
                if after < row.id <= high_water
                and row.status in cleanup_temp.CLEANUP_STATUSES
                and row.local_path is not None
            ][:batch_size]
            checked_batches.append([row.id for row in selected])
            return FakeScalars(selected)

    monkeypatch.setattr(cleanup_temp, "SessionLocal", lambda: HighWaterSession(rows))
    monkeypatch.setattr(
        cleanup_temp,
        "get_settings",
        lambda: SimpleNamespace(
            rejected_retention_days=7,
            temp_storage_dir=safe_root,
            temp_cleanup_batch_size=batch_size,
            temp_part_retention_seconds=3600,
        ),
    )

    async def no_outbox(*_):  # noqa: ANN002
        return False

    monkeypatch.setattr(cleanup_temp, "_has_pending_terminal_upload_notification", no_outbox)

    deleted, state = await cleanup_temp.cleanup_temp_pass(None)
    assert deleted == 2
    assert checked_batches[-1] == [1, 3]
    assert state == cleanup_temp.CleanupScanState(last_id=3, high_water_id=5)

    late_low.status = UploadStatus.uploaded
    add_row(6)
    deleted, state = await cleanup_temp.cleanup_temp_pass(state)
    assert deleted == 2
    assert checked_batches[-1] == [4, 5]
    assert state is None

    add_row(7)
    deleted, state = await cleanup_temp.cleanup_temp_pass(state)
    assert deleted == 2
    assert checked_batches[-1] == [2, 6]
    assert state == cleanup_temp.CleanupScanState(last_id=6, high_water_id=7)
    assert late_low.status == UploadStatus.deleted_temp
    assert len(checked_batches[-1]) <= batch_size


def test_cleanup_main_uses_asyncio_run(monkeypatch) -> None:  # noqa: ANN001
    called = {}
    monkeypatch.setattr(cleanup_temp, "get_settings", lambda: SimpleNamespace(log_level="INFO"))

    def fake_run(coro):  # noqa: ANN001
        called["coro"] = coro
        coro.close()

    monkeypatch.setattr(cleanup_temp.asyncio, "run", fake_run)
    monkeypatch.setattr(
        cleanup_temp.asyncio,
        "get_event_loop",
        lambda: (_ for _ in ()).throw(AssertionError("legacy loop")),
    )

    cleanup_temp.main()

    assert called["coro"].cr_code.co_name == "main_async"


@pytest.mark.anyio
async def test_pending_terminal_upload_notification_guard_sql_includes_rejection_events() -> None:
    statement_holder = {}

    class CaptureSession(FakeSession):
        async def scalar(self, statement):  # noqa: ANN001
            statement_holder["sql"] = str(statement.compile(compile_kwargs={"literal_binds": True}))
            return False

    await cleanup_temp._has_pending_terminal_upload_notification(CaptureSession([]), 42)  # noqa: SLF001

    sql = statement_holder["sql"]
    assert "upload_result_admin" in sql
    assert "upload_result_user" in sql
    assert "upload_rejected" in sql
    assert "pending" in sql
    assert "processing" in sql


class OutboxAwareSession(FakeSession):
    def __init__(self, rows, outbox_events) -> None:  # noqa: ANN001
        super().__init__(rows)
        self.outbox_events = outbox_events

    async def scalar(self, statement):  # noqa: ANN001
        text = str(statement.compile(compile_kwargs={"literal_binds": True}))
        if "max" in text.lower():
            return await super().scalar(statement)
        terminal_events = {
            event.value if hasattr(event, "value") else event
            for event in cleanup_temp.TERMINAL_UPLOAD_NOTIFICATION_EVENT_TYPES
        }
        deliverable_statuses = {
            status.value if hasattr(status, "value") else status
            for status in cleanup_temp.DELIVERABLE_OUTBOX_STATUSES
        }
        return any(
            event.event_type in terminal_events and event.status in deliverable_statuses
            for event in self.outbox_events
        )


def _rejected_upload_row(path):  # noqa: ANN001, ANN202
    return SimpleNamespace(
        id=20,
        request_code="REQ-000020",
        status=UploadStatus.rejected,
        local_path=str(path),
        created_at=datetime.now(UTC) - timedelta(days=10),
        reject_reason="bad file",
    )


async def _run_cleanup_with_outbox(monkeypatch, tmp_path, event_status):  # noqa: ANN001, ANN202
    path = tmp_path / "REQ-000020" / "file.txt"
    path.parent.mkdir()
    path.write_text("content")
    row = _rejected_upload_row(path)
    event = SimpleNamespace(
        request_id=20,
        event_type="upload_rejected",
        status=event_status.value if hasattr(event_status, "value") else event_status,
        payload={"request_id": 20, "status": "rejected"},
    )
    monkeypatch.setattr(cleanup_temp, "SessionLocal", lambda: OutboxAwareSession([row], [event]))
    monkeypatch.setattr(
        cleanup_temp,
        "get_settings",
        lambda: SimpleNamespace(
            rejected_retention_days=7,
            temp_storage_dir=tmp_path,
            temp_cleanup_batch_size=100,
            temp_part_retention_seconds=3600,
        ),
    )
    deleted = await cleanup_temp.cleanup_temp()
    return deleted, row, event, path


@pytest.mark.anyio
@pytest.mark.parametrize(
    "event_status",
    [TelegramOutboxStatus.pending, TelegramOutboxStatus.processing],
)
async def test_cleanup_keeps_rejected_status_while_rejection_outbox_deliverable(
    monkeypatch, tmp_path, event_status
) -> None:  # noqa: ANN001
    deleted, row, event, path = await _run_cleanup_with_outbox(monkeypatch, tmp_path, event_status)

    assert deleted == 1
    assert row.status == UploadStatus.rejected
    assert event.status == event_status.value
    assert not path.exists()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "event_status",
    [TelegramOutboxStatus.sent, TelegramOutboxStatus.discarded, TelegramOutboxStatus.dead],
)
async def test_cleanup_marks_deleted_after_rejection_outbox_terminal(
    monkeypatch, tmp_path, event_status
) -> None:  # noqa: ANN001
    deleted, row, _event, path = await _run_cleanup_with_outbox(monkeypatch, tmp_path, event_status)

    assert deleted == 1
    assert row.status == UploadStatus.deleted_temp
    assert not path.exists()


@pytest.mark.anyio
@pytest.mark.parametrize("event_type", ["upload_result_admin", "upload_result_user"])
async def test_cleanup_still_keeps_status_while_upload_result_outbox_pending(
    monkeypatch, tmp_path, event_type
) -> None:  # noqa: ANN001
    path = tmp_path / "REQ-RESULT" / "file.txt"
    path.parent.mkdir()
    path.write_text("content")
    row = SimpleNamespace(
        id=20,
        status=UploadStatus.uploaded,
        local_path=str(path),
        created_at=datetime.now(UTC),
    )
    event = SimpleNamespace(
        request_id=20,
        event_type=event_type,
        status=TelegramOutboxStatus.pending.value,
        payload={"request_id": 20, "status": "uploaded"},
    )
    monkeypatch.setattr(cleanup_temp, "SessionLocal", lambda: OutboxAwareSession([row], [event]))
    monkeypatch.setattr(
        cleanup_temp,
        "get_settings",
        lambda: SimpleNamespace(
            rejected_retention_days=7,
            temp_storage_dir=tmp_path,
            temp_cleanup_batch_size=100,
            temp_part_retention_seconds=3600,
        ),
    )

    assert await cleanup_temp.cleanup_temp() == 1
    assert row.status == UploadStatus.uploaded
    assert not path.exists()
