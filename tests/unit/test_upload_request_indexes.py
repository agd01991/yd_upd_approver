from collections.abc import Callable
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.models import UploadRequest


class RecordingOperations:
    def __init__(self) -> None:
        self.created_indexes: list[tuple[str, str, list[str]]] = []
        self.dropped_indexes: list[tuple[str, str | None]] = []

    def __getattr__(self, _name: str) -> Callable[..., None]:
        return lambda *_args, **_kwargs: None

    def create_index(self, name: str, table_name: str, columns: list[str], **_kwargs: Any) -> None:
        self.created_indexes.append((name, table_name, columns))

    def drop_index(self, name: str, *, table_name: str | None = None) -> None:
        self.dropped_indexes.append((name, table_name))


def test_upload_request_metadata_has_global_ordering_index() -> None:
    indexes = {
        index.name: [column.name for column in index.columns]
        for index in UploadRequest.__table__.indexes
    }

    assert indexes["ix_upload_requests_created_id"] == ["created_at", "id"]
    assert indexes["ix_upload_requests_user_created_id"] == ["user_id", "created_at", "id"]
    assert indexes["ix_upload_requests_status_created_id"] == ["status", "created_at", "id"]


def test_upload_ordering_index_migration_creates_and_drops_global_ordering_index(
    monkeypatch,
) -> None:  # noqa: ANN001
    migration = (
        ScriptDirectory.from_config(Config("alembic.ini"))
        .get_revision("0010_upload_created_index")
        .module
    )
    assert migration.down_revision == "0009_db_integrity"
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()
    migration.downgrade()

    assert (
        "ix_upload_requests_created_id",
        "upload_requests",
        ["created_at", "id"],
    ) in operations.created_indexes
    assert (
        "ix_upload_requests_created_id",
        "upload_requests",
    ) in operations.dropped_indexes
