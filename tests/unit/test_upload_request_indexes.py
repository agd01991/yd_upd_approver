from collections.abc import Callable
from typing import Any

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.models import UploadRequest


class RecordingOperations:
    def __init__(self) -> None:
        self.created_indexes: list[tuple[str, str, list[str], dict[str, Any]]] = []
        self.dropped_indexes: list[tuple[str, str | None, dict[str, Any]]] = []

    def __getattr__(self, _name: str) -> Callable[..., None]:
        return lambda *_args, **_kwargs: None

    def create_index(self, name: str, table_name: str, columns: list[str], **kwargs: Any) -> None:
        self.created_indexes.append((name, table_name, columns, kwargs))

    def drop_index(self, name: str, *, table_name: str | None = None, **kwargs: Any) -> None:
        self.dropped_indexes.append((name, table_name, kwargs))


def test_upload_request_metadata_has_global_ordering_index() -> None:
    indexes = {
        index.name: [column.name for column in index.columns]
        for index in UploadRequest.__table__.indexes
    }

    assert indexes["ix_upload_requests_created_id"] == ["created_at", "id"]
    assert indexes["ix_upload_requests_user_created_id"] == ["user_id", "created_at", "id"]
    assert indexes["ix_upload_requests_status_created_id"] == ["status", "created_at", "id"]


def test_upload_ordering_index_migration_creates_and_validates_global_ordering_index(
    monkeypatch,
) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    target = _target(migration)
    lookups = iter((None, _expected_index(migration)))
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: target)
    monkeypatch.setattr(migration, "_find_existing_index", lambda actual_target: next(lookups))

    migration.upgrade()
    migration.downgrade()

    assert operations.created_indexes == [
        (
            "ix_upload_requests_created_id",
            "upload_requests",
            ["created_at", "id"],
            {"schema": "public", "if_not_exists": True},
        )
    ]
    assert operations.dropped_indexes == [
        (
            "ix_upload_requests_created_id",
            "upload_requests",
            {"schema": "public"},
        )
    ]


def test_upload_ordering_index_migration_accepts_concurrently_created_index(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    target = _target(migration)
    lookups = iter((None, _expected_index(migration)))
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: target)
    monkeypatch.setattr(migration, "_find_existing_index", lambda actual_target: next(lookups))

    migration.upgrade()

    assert len(operations.created_indexes) == 1
    assert operations.created_indexes[0][3] == {"schema": target.schema, "if_not_exists": True}
    assert operations.dropped_indexes == []


def test_upload_ordering_index_migration_rejects_conflict_created_during_race(
    monkeypatch,
) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    target = _target(migration)
    lookups = iter((None, _expected_index(migration, table_oid=99)))
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: target)
    monkeypatch.setattr(migration, "_find_existing_index", lambda actual_target: next(lookups))

    with pytest.raises(RuntimeError, match="unexpected definition"):
        migration.upgrade()

    assert len(operations.created_indexes) == 1
    assert operations.created_indexes[0][3]["if_not_exists"] is True
    assert operations.dropped_indexes == []


def test_upload_ordering_index_migration_rejects_missing_index_after_creation(
    monkeypatch,
) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    target = _target(migration)
    lookups = iter((None, None))
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: target)
    monkeypatch.setattr(migration, "_find_existing_index", lambda actual_target: next(lookups))

    with pytest.raises(RuntimeError, match="was not found after creation"):
        migration.upgrade()

    assert len(operations.created_indexes) == 1
    assert operations.dropped_indexes == []


def _expected_index(migration, **changes: Any):  # noqa: ANN001
    fields = {
        "schema": "public",
        "table_oid": 42,
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


def _target(migration, **changes: Any):  # noqa: ANN001
    fields = {"oid": 42, "schema_oid": 2200, "schema": "public", "name": "upload_requests"}
    fields.update(changes)
    return migration._TargetTable(**fields)


def _migration_module():
    return (
        ScriptDirectory.from_config(Config("alembic.ini"))
        .get_revision("0010_upload_created_index")
        .module
    )


def test_target_table_resolution_uses_resolved_public_relation(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()

    class Result:
        def mappings(self):
            return self

        def one_or_none(self):
            return {"oid": 42, "schema_oid": 2200, "schema": "public", "name": "upload_requests"}

    class Bind:
        def execute(self, _statement):
            return Result()

    class Operations:
        def get_bind(self):
            return Bind()

    monkeypatch.setattr(migration, "op", Operations())

    assert migration._resolve_target_table() == _target(migration)


def test_upload_ordering_index_migration_accepts_correct_intermediate_index(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    target = _target(migration)
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: target)
    monkeypatch.setattr(
        migration, "_find_existing_index", lambda actual_target: _expected_index(migration)
    )

    migration.upgrade()

    assert operations.created_indexes == []
    assert operations.dropped_indexes == []


def test_upload_ordering_index_migration_rejects_wrong_columns(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: _target(migration))
    monkeypatch.setattr(
        migration,
        "_find_existing_index",
        lambda actual_target: _expected_index(migration, key_columns=("status", "id")),
    )

    with pytest.raises(RuntimeError, match="ix_upload_requests_created_id"):
        migration.upgrade()

    assert operations.created_indexes == []
    assert operations.dropped_indexes == []


def test_upload_ordering_index_migration_rejects_index_on_another_table(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: _target(migration))
    monkeypatch.setattr(
        migration,
        "_find_existing_index",
        lambda actual_target: _expected_index(migration, table_name="users"),
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
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: _target(migration))
    monkeypatch.setattr(
        migration,
        "_find_existing_index",
        lambda actual_target: _expected_index(migration, **changes),
    )

    with pytest.raises(RuntimeError):
        migration.upgrade()

    assert operations.created_indexes == []
    assert operations.dropped_indexes == []


def test_upload_ordering_index_migration_ignores_shadow_schema_index(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    target = _target(migration)
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: target)
    lookups = iter((None, _expected_index(migration)))
    monkeypatch.setattr(migration, "_find_existing_index", lambda actual_target: next(lookups))

    migration.upgrade()

    assert operations.created_indexes[0][3] == {"schema": "public", "if_not_exists": True}


def test_upload_ordering_index_migration_rejects_matching_columns_on_other_oid(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: _target(migration))
    monkeypatch.setattr(
        migration,
        "_find_existing_index",
        lambda actual_target: _expected_index(migration, table_oid=99),
    )

    with pytest.raises(RuntimeError, match="unexpected definition"):
        migration.upgrade()
