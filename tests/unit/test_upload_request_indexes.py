from collections.abc import Callable
from typing import Any

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.models import UploadRequest


class RecordingOperations:
    def __init__(self) -> None:
        self.created_indexes: list[tuple[str, str, list[str], dict[str, Any]]] = []
        self.dropped_indexes: list[tuple[str, str | None]] = []

    def __getattr__(self, _name: str) -> Callable[..., None]:
        return lambda *_args, **_kwargs: None

    def create_index(self, name: str, table_name: str, columns: list[str], **kwargs: Any) -> None:
        self.created_indexes.append((name, table_name, columns, kwargs))

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
    monkeypatch.setattr(migration, "_find_existing_index", lambda: None)

    migration.upgrade()
    migration.downgrade()

    assert (
        "ix_upload_requests_created_id",
        "upload_requests",
        ["created_at", "id"],
        {},
    ) in operations.created_indexes
    assert (
        "ix_upload_requests_created_id",
        "upload_requests",
    ) in operations.dropped_indexes


def _expected_index(migration, **changes: Any):  # noqa: ANN001
    fields = {
        "schema": "public",
        "table_schema": "public",
        "table_name": "upload_requests",
        "key_columns": ("created_at", "id"),
        "key_column_count": 2,
        "total_column_count": 2,
        "access_method": "btree",
        "is_unique": False,
        "is_partial": False,
        "is_expression": False,
        "is_valid": True,
        "is_ready": True,
    }
    fields.update(changes)
    return migration._IndexSignature(**fields)


def _migration_module():
    return (
        ScriptDirectory.from_config(Config("alembic.ini"))
        .get_revision("0010_upload_created_index")
        .module
    )


def test_upload_ordering_index_migration_accepts_correct_intermediate_index(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_find_existing_index", lambda: _expected_index(migration))

    migration.upgrade()

    assert operations.created_indexes == []
    assert operations.dropped_indexes == []


def test_upload_ordering_index_migration_rejects_wrong_columns(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(
        migration,
        "_find_existing_index",
        lambda: _expected_index(migration, key_columns=("status", "id")),
    )

    with pytest.raises(RuntimeError, match="ix_upload_requests_created_id"):
        migration.upgrade()

    assert operations.created_indexes == []
    assert operations.dropped_indexes == []


def test_upload_ordering_index_migration_rejects_index_on_another_table(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(
        migration, "_find_existing_index", lambda: _expected_index(migration, table_name="users")
    )

    with pytest.raises(RuntimeError, match="found table public.users"):
        migration.upgrade()

    assert operations.created_indexes == []
    assert operations.dropped_indexes == []


@pytest.mark.parametrize(
    "changes",
    [
        {"is_unique": True},
        {"is_partial": True},
        {"is_expression": True},
        {"total_column_count": 3},
        {"key_columns": ("id", "created_at")},
        {"is_valid": False},
    ],
)
def test_upload_ordering_index_migration_rejects_incompatible_index_kind(
    monkeypatch, changes: dict[str, Any]
) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(
        migration, "_find_existing_index", lambda: _expected_index(migration, **changes)
    )

    with pytest.raises(RuntimeError):
        migration.upgrade()

    assert operations.created_indexes == []
    assert operations.dropped_indexes == []
