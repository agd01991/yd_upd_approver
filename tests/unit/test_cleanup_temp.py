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
            status=UploadStatus.uploaded,
            local_path=str(old_file),
            created_at=datetime.now(UTC) - timedelta(days=10),
        ),
        SimpleNamespace(
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
