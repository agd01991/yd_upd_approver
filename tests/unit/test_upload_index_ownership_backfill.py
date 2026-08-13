import subprocess
import sys
from dataclasses import replace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


def _migration():
    return (
        ScriptDirectory.from_config(Config("alembic.ini"))
        .get_revision("0011_upload_index_ownership")
        .module
    )


def _index(migration, **changes):  # noqa: ANN001, ANN003
    value = migration._IndexSignature(
        index_oid=84,
        schema="public",
        table_oid=42,
        table_schema="public",
        table_name="upload_requests",
        table_kind="r",
        index_name="ix_upload_requests_created_id",
        key_columns=("created_at", "id"),
        key_options=(0, 0),
        key_column_count=2,
        total_column_count=2,
        access_method="btree",
        is_unique=False,
        is_partial=False,
        is_expression=False,
        is_valid=True,
        is_ready=True,
        ownership_comment=migration._INDEX_OWNERSHIP_MARKER,
    )
    return replace(value, **changes)


def _target(migration):  # noqa: ANN001
    return migration._TargetTable(42, 2200, "public", "upload_requests")


class Operations:
    def __init__(self) -> None:
        self.executed = []

    def execute(self, statement):  # noqa: ANN001
        self.executed.append(str(statement))


def test_marker_matches_revision_0010() -> None:
    migration = _migration()
    old = (
        ScriptDirectory.from_config(Config("alembic.ini"))
        .get_revision("0010_upload_created_index")
        .module
    )
    assert migration._INDEX_OWNERSHIP_MARKER == old._INDEX_OWNERSHIP_MARKER


def test_online_catalog_query_casts_table_relkind_to_text() -> None:
    migration = _migration()
    assert "t.relkind::text AS table_kind" in migration._INDEX_SELECT
    assert "t.relkind AS table_kind" not in migration._INDEX_SELECT


def test_owned_index_is_idempotent(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    operations = Operations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_owned_indexes", lambda: [_index(migration)])
    migration._online_upgrade()
    assert operations.executed == []


def test_null_comment_is_backfilled_and_post_validated(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    operations = Operations()
    candidate = _index(migration, ownership_comment=None)
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_quote", lambda value: f'"{value}"')
    monkeypatch.setattr(migration, "_owned_indexes", lambda: [])
    monkeypatch.setattr(migration, "_resolve_application_target", lambda: _target(migration))
    monkeypatch.setattr(migration, "_target_index", lambda _target: [candidate])
    monkeypatch.setattr(migration, "_rows", lambda *_args, **_kwargs: [_index(migration)])
    migration._online_upgrade()
    assert len(operations.executed) == 1
    assert 'COMMENT ON INDEX "public"."ix_upload_requests_created_id"' in operations.executed[0]


@pytest.mark.parametrize(
    "comment", [pytest.param("", id="empty-non-null"), pytest.param("another-owner", id="foreign")]
)
def test_non_null_comment_is_not_overwritten(monkeypatch, comment: str) -> None:  # noqa: ANN001
    migration = _migration()
    operations = Operations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_owned_indexes", lambda: [])
    monkeypatch.setattr(migration, "_resolve_application_target", lambda: _target(migration))
    monkeypatch.setattr(
        migration, "_target_index", lambda _target: [_index(migration, ownership_comment=comment)]
    )
    with pytest.raises(RuntimeError, match="ownership conflict"):
        migration._online_upgrade()
    assert operations.executed == []


@pytest.mark.parametrize(
    "changes",
    [
        {"key_columns": ("id", "created_at")},
        {"key_options": (1, 0)},
        {"key_options": (2, 0)},
        {"key_options": (0, 1)},
        {"key_options": (0, 2)},
        {"table_kind": "m"},
        {"schema": "pgapp", "table_schema": "pgapp"},
        {"schema": "pg_catalog", "table_schema": "pg_catalog"},
        {"is_partial": True},
        {"is_expression": True},
        {"is_valid": False},
        {"is_ready": False},
    ],
)
def test_full_signature_is_enforced(changes: dict[str, object]) -> None:
    migration = _migration()
    expected = changes == {"schema": "pgapp", "table_schema": "pgapp"}
    assert migration._matches(_index(migration, **changes)) is expected


def test_expected_marker_on_incompatible_object_is_not_ignored(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    monkeypatch.setattr(migration, "_owned_indexes", lambda: [_index(migration, table_kind="m")])
    with pytest.raises(RuntimeError, match="incompatible index signature"):
        migration._online_upgrade()


def test_multiple_owned_indexes_are_ambiguous(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    monkeypatch.setattr(
        migration, "_owned_indexes", lambda: [_index(migration), _index(migration, index_oid=85)]
    )
    with pytest.raises(RuntimeError, match="ambiguous owned indexes"):
        migration._online_upgrade()


@pytest.mark.parametrize(
    "message", ["application target not found", "ambiguous application targets"]
)
def test_target_resolution_failure_does_not_consider_global_candidate(monkeypatch, message) -> None:  # noqa: ANN001
    migration = _migration()
    monkeypatch.setattr(migration, "_owned_indexes", lambda: [])

    def fail():
        raise RuntimeError(message)

    monkeypatch.setattr(migration, "_resolve_application_target", fail)
    with pytest.raises(RuntimeError, match=message):
        migration._online_upgrade()


def test_post_marker_validation_failure_fails_upgrade(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    operations = Operations()
    candidate = _index(migration, ownership_comment=None)
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_quote", lambda value: value)
    monkeypatch.setattr(migration, "_owned_indexes", lambda: [])
    monkeypatch.setattr(migration, "_resolve_application_target", lambda: _target(migration))
    monkeypatch.setattr(migration, "_target_index", lambda _target: [candidate])
    monkeypatch.setattr(migration, "_rows", lambda *_args, **_kwargs: [candidate])
    with pytest.raises(RuntimeError, match="marker post-validation failure"):
        migration._online_upgrade()


def test_downgrade_preserves_marker(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    operations = Operations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(migration, "_owned_indexes", lambda: [_index(migration)])
    migration.downgrade()
    assert operations.executed == []


def test_downgrade_requires_marker(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(migration, "_owned_indexes", lambda: [])
    with pytest.raises(RuntimeError, match="managed index not found"):
        migration.downgrade()


@pytest.mark.parametrize(
    ("command", "is_downgrade"),
    [
        (["upgrade", "0010_upload_created_index:0011_upload_index_ownership", "--sql"], False),
        (["downgrade", "0011_upload_index_ownership:0010_upload_created_index", "--sql"], True),
    ],
)
def test_offline_runtime_sql_has_safe_semantics(command: list[str], is_downgrade: bool) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", *command], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    sql = result.stdout
    assert "ARRAY[0,0]::smallint[]" in sql
    assert "unnest(x.indoption)" in sql and "WITH ORDINALITY" in sql
    assert "t.relkind IN ('r','p')" in sql
    assert "left(n.nspname,3)<>'pg_'" in sql
    assert "NOT LIKE 'pg_%'" not in sql
    assert "yd_upd_approver:alembic:0010_upload_created_index" in sql
    if is_downgrade:
        assert "DROP INDEX" not in sql
        assert "managed index not found" in sql
    else:
        assert "ix_upload_requests_user_created_id" in sql
        assert "ix_upload_requests_status_created_id" in sql
        assert "COMMENT ON INDEX %I.%I IS %L" in sql
        assert "existing_comment IS NOT NULL" in sql
        assert "marker post-validation failure" in sql
