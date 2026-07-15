from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.db.models import UploadStatus
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

    monkeypatch.setattr(cleanup_temp, "_has_deliverable_upload_result_outbox", no_outbox)

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

    monkeypatch.setattr(cleanup_temp, "_has_deliverable_upload_result_outbox", pending)

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

    monkeypatch.setattr(cleanup_temp, "_has_deliverable_upload_result_outbox", no_outbox)

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
