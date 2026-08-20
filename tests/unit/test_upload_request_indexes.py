import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.models import UploadRequest


def test_manual_qa_upload_index_query_uses_explicit_application_schema() -> None:
    manual_qa = Path("docs/MANUAL_QA.md").read_text()
    first_placeholder = manual_qa.index("REPLACE_WITH_APPLICATION_SCHEMA")
    placeholder_offset = manual_qa.index("REPLACE_WITH_APPLICATION_SCHEMA", first_placeholder + 1)
    query_start = manual_qa.rindex("```sql", 0, placeholder_offset) + len("```sql")
    query = manual_qa[query_start : manual_qa.index("```", placeholder_offset)]

    assert "to_regclass('upload_requests')" not in query
    assert "upload_requests'::regclass" not in query
    assert "REPLACE_WITH_APPLICATION_SCHEMA" in query
    assert "JOIN pg_catalog.pg_namespace AS table_namespace" in query
    assert "JOIN pg_catalog.pg_class AS table_class" in query
    assert "JOIN pg_catalog.pg_index AS index_definition" in query
    assert "JOIN pg_catalog.pg_class AS index_class" in query
    assert "JOIN pg_catalog.pg_attribute AS attribute" in query
    assert "pg_catalog.array_agg" in query
    assert "pg_catalog.unnest" in query
    assert "pg_catalog.pg_get_indexdef" in query
    assert "pg_catalog.obj_description" in query
    for catalog_relation in ("pg_namespace", "pg_class", "pg_index", "pg_attribute"):
        assert not re.search(
            rf"\bJOIN\s+(?!pg_catalog\.){catalog_relation}\b", query, re.IGNORECASE
        )
    for catalog_function in ("array_agg", "unnest", "pg_get_indexdef", "obj_description"):
        assert not re.search(rf"(?<!pg_catalog\.)\b{catalog_function}\s*\(", query, re.IGNORECASE)
    assert "table_class.relnamespace = table_namespace.oid" in query
    assert "index_definition.indrelid = target_table.oid" in query
    assert "index_class.relnamespace = target_table.relnamespace" in query
    assert "pg_catalog.array_agg(attribute.attname ORDER BY key.ordinality) AS key_columns" in query
    assert "pg_catalog.obj_description(index_class.oid, 'pg_class') AS ownership_comment" in query
    assert "yd_upd_approver:alembic:0010_upload_created_index" in manual_qa


@pytest.fixture(autouse=True)
def _use_online_migration_mode(monkeypatch) -> None:  # noqa: ANN001
    """Unit tests exercise the online branch without an Alembic EnvironmentContext."""
    monkeypatch.setattr(_migration_module().context, "is_offline_mode", lambda: False)


class RecordingOperations:
    def __init__(self) -> None:
        self.created_indexes: list[tuple[str, str, list[str], dict[str, Any]]] = []
        self.dropped_indexes: list[tuple[str, str | None, dict[str, Any]]] = []
        self.executed: list[Any] = []

    def __getattr__(self, _name: str) -> Callable[..., None]:
        return lambda *_args, **_kwargs: None

    def create_index(self, name: str, table_name: str, columns: list[str], **kwargs: Any) -> None:
        self.created_indexes.append((name, table_name, columns, kwargs))

    def drop_index(self, name: str, *, table_name: str | None = None, **kwargs: Any) -> None:
        self.dropped_indexes.append((name, table_name, kwargs))

    def execute(self, statement: Any) -> None:
        self.executed.append(statement)


def test_upload_request_metadata_has_global_ordering_index() -> None:
    indexes = {
        index.name: [column.name for column in index.columns]
        for index in UploadRequest.__table__.indexes
    }

    assert indexes["ix_upload_requests_created_id"] == ["created_at", "id"]
    assert indexes["ix_upload_requests_user_created_id"] == ["user_id", "created_at", "id"]
    assert indexes["ix_upload_requests_status_created_id"] == ["status", "created_at", "id"]


def test_anchor_opclasses_are_validated_from_ordered_catalog_oids() -> None:
    sql = _migration_module()._anchor_predicate("x", ("status", "created_at", "id"))

    assert "unnest(x.indclass) WITH ORDINALITY" in sql
    assert "ic.ordinality = k.ordinality" in sql
    assert "LEFT JOIN pg_opclass opc ON opc.oid = ic.opclass_oid" in sql
    assert "opc.opcmethod = am.oid AND opc.opcdefault" in sql
    assert "opc.opcintype = a.atttypid" in sql
    assert "typ.typtype = 'e'" in sql
    assert "opc.opcintype = 'pg_catalog.anyenum'::regtype" in sql
    assert "count(*) = x.indnkeyatts" in sql
    assert "COALESCE(bool_and" in sql
    assert "enum_ops" not in sql
    assert "array_agg(a.atttypid ORDER BY k.ordinality)" in sql
    assert "'pg_catalog.int4'::regtype::oid" in sql
    assert "'pg_catalog.timestamptz'::regtype::oid" in sql
    assert "typ.typname = 'uploadstatus'" in sql
    assert "pg_enum enum" in sql and "enum.enumsortorder" in sql
    assert "array_agg(enum.enumlabel::text ORDER BY enum.enumsortorder)" in sql
    assert "min(typ.oid)" not in sql
    assert "to_regtype('uploadstatus')" not in sql


def test_0010_and_0011_share_independent_ordered_anchor_type_identity() -> None:
    migration_0010 = _migration_module()
    migration_0011 = (
        ScriptDirectory.from_config(Config("alembic.ini"))
        .get_revision("0011_upload_index_ownership")
        .module
    )

    for columns in (
        ("user_id", "created_at", "id"),
        ("status", "created_at", "id"),
    ):
        expected = migration_0010._expected_anchor_type_oids(columns)
        assert expected == migration_0011._expected_anchor_type_oids(columns)
        for sql in (
            migration_0010._anchor_predicate("x", columns),
            migration_0011._anchor_predicate("x", columns),
        ):
            assert "array_agg(a.atttypid ORDER BY k.ordinality)" in sql
            assert f"= {expected}" in sql

    status_sql = migration_0010._expected_anchor_type_oids(("status", "created_at", "id"))
    assert "a.atttypid" not in status_sql
    assert "ARRAY['new','stored','pending_approval'" in status_sql
    assert "array_agg(enum.enumlabel::text ORDER BY enum.enumsortorder)" in status_sql


def test_upload_ordering_index_migration_creates_and_validates_global_ordering_index(
    monkeypatch,
) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    target = _target(migration)
    lookups = iter((None, _expected_index(migration), _expected_index(migration)))
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: target)
    monkeypatch.setattr(migration, "_find_existing_index", lambda actual_target: next(lookups))
    monkeypatch.setattr(migration, "_downgrade_candidates", lambda: [_expected_index(migration)])

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


@pytest.mark.parametrize(
    ("command", "required"),
    [
        (
            ["upgrade", "0009_db_integrity:0010_upload_created_index", "--sql"],
            ["DO $$", "CREATE INDEX IF NOT EXISTS", "ix_upload_requests_created_id", "created_at"],
        ),
        (
            ["downgrade", "0010_upload_created_index:0009_db_integrity", "--sql"],
            ["DO $$", "DROP INDEX", "ix_upload_requests_created_id", "candidate_count"],
        ),
    ],
)
def test_0010_generates_safe_offline_sql_without_database(
    command: list[str], required: list[str]
) -> None:
    result = subprocess.run(  # noqa: S603 -- fixed local Alembic command under test
        [sys.executable, "-m", "alembic", *command],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
    assert "Traceback" not in result.stdout + result.stderr
    for value in required:
        assert value in result.stdout
    assert "unnest(x.indoption) WITH ORDINALITY" in result.stdout
    assert "ARRAY[0, 0]::smallint[]" in result.stdout
    if command[0] == "downgrade":
        assert "t.relkind IN ('r', 'p')" in result.stdout
        assert "left(ins.nspname, 3) <> 'pg_'" in result.stdout
        assert "NOT LIKE 'pg_%'" not in result.stdout
        assert (
            "obj_description(i.oid, 'pg_class') = "
            "'yd_upd_approver:alembic:0010_upload_created_index'"
        ) in result.stdout
    else:
        for columns in (
            "ARRAY['user_id','created_at','id']::name[]",
            "ARRAY['status','created_at','id']::name[]",
        ):
            assert columns in result.stdout
        for predicate in (
            "x.indnkeyatts = 3",
            "x.indnatts = 3",
            "am.amname = 'btree'",
            "NOT x.indisunique",
            "x.indpred IS NULL",
            "x.indexprs IS NULL",
            "NOT x.indisexclusion",
            "x.indisvalid",
            "x.indisready",
            "ARRAY[0,0,0]::smallint[]",
            "unnest(x.indclass)",
            "opc.opcdefault",
            "i.relnamespace=c.relnamespace",
        ):
            assert result.stdout.count(predicate) >= 2
        assert "COMMENT ON INDEX %I.%I IS %L" in result.stdout
        assert "existing_comment IS NOT NULL" in result.stdout
        assert "ownership marker was not stored" in result.stdout


def test_upload_ordering_index_downgrade_refuses_ambiguous_candidates(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(
        migration,
        "_downgrade_candidates",
        lambda: [_expected_index(migration), _expected_index(migration, schema="other")],
    )

    with pytest.raises(RuntimeError, match="ambiguous compatible indexes"):
        migration.downgrade()

    assert operations.dropped_indexes == []


def test_upload_ordering_index_downgrade_rejects_missing_candidate(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_downgrade_candidates", lambda: [])

    with pytest.raises(RuntimeError, match="no compatible managed index"):
        migration.downgrade()

    assert operations.dropped_indexes == []


@pytest.mark.parametrize("comment", [None, "another-owner"])
def test_downgrade_candidates_require_exact_ownership_marker(monkeypatch, comment) -> None:  # noqa: ANN001
    migration = _migration_module()
    monkeypatch.setattr(
        migration,
        "_index_rows",
        lambda *_args, **_kwargs: [_expected_index(migration, ownership_comment=comment)],
    )

    assert migration._downgrade_candidates() == []


def test_downgrade_candidates_select_owned_and_ignore_unowned(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    owned = _expected_index(migration)
    unowned = _expected_index(migration, schema="other", ownership_comment=None)
    monkeypatch.setattr(migration, "_index_rows", lambda *_args, **_kwargs: [owned, unowned])

    assert migration._downgrade_candidates() == [owned]


def test_upgrade_adopts_uncommented_compatible_index(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_quote_identifier", lambda value: f'"{value}"')
    target = _target(migration)
    unowned = _expected_index(migration, ownership_comment=None)
    lookups = iter((unowned, _expected_index(migration)))
    locked = _expected_index(migration, ownership_comment=None)
    marked = _expected_index(migration)
    oid_lookups = iter(([locked], [marked]))
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: target)
    monkeypatch.setattr(migration, "_find_existing_index", lambda _target: next(lookups))
    monkeypatch.setattr(migration, "_index_rows", lambda *_args, **_kwargs: next(oid_lookups))

    migration.upgrade()

    assert operations.created_indexes == []
    statement = str(operations.executed[1])
    assert 'COMMENT ON INDEX "public"."__yd_0010_adopt_84"' in statement
    assert migration._INDEX_OWNERSHIP_MARKER in statement


def test_upgrade_refuses_foreign_comment_without_overwriting_it(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: _target(migration))
    monkeypatch.setattr(
        migration,
        "_find_existing_index",
        lambda _target: _expected_index(migration, ownership_comment="another-owner"),
    )

    with pytest.raises(RuntimeError, match="ownership conflict"):
        migration.upgrade()

    assert operations.executed == []


def test_upgrade_fails_when_ownership_marker_is_not_persisted(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_quote_identifier", lambda value: f'"{value}"')
    target = _target(migration)
    unowned = _expected_index(migration, ownership_comment=None)
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: target)
    monkeypatch.setattr(migration, "_find_existing_index", lambda _target: unowned)
    monkeypatch.setattr(migration, "_index_rows", lambda *_args, **_kwargs: [unowned])

    with pytest.raises(RuntimeError, match="ownership marker was not stored"):
        migration.upgrade()

    assert len(operations.executed) == 2


def test_upload_ordering_index_migration_accepts_concurrently_created_index(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    target = _target(migration)
    lookups = iter((None, _expected_index(migration), _expected_index(migration)))
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
        "index_oid": 84,
        "index_schema_oid": 2200,
        "schema": "public",
        "table_oid": 42,
        "table_schema": "public",
        "table_name": "upload_requests",
        "key_columns": ("created_at", "id"),
        "key_options": (0, 0),
        "key_opclasses": (3127, 1978),
        "expected_key_opclasses": (3127, 1978),
        "key_column_count": 2,
        "total_column_count": 2,
        "access_method": "btree",
        "is_unique": False,
        "is_partial": False,
        "is_expression": False,
        "is_exclusion": False,
        "is_valid": True,
        "is_ready": True,
        "ownership_comment": "yd_upd_approver:alembic:0010_upload_created_index",
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

        def all(self):
            return [{"oid": 42, "schema_oid": 2200, "schema": "public", "name": "upload_requests"}]

    class Bind:
        def execute(self, _statement):
            return Result()

    class Operations:
        def get_bind(self):
            return Bind()

    monkeypatch.setattr(migration, "op", Operations())

    assert migration._resolve_target_table() == _target(migration)


def test_0010_online_and_offline_target_lookups_share_complete_anchor_fingerprints(
    monkeypatch,
) -> None:  # noqa: ANN001
    migration = _migration_module()
    statements: list[str] = []

    class Result:
        def mappings(self):
            return self

        def all(self):
            return []

    class Bind:
        def execute(self, statement):  # noqa: ANN001
            statements.append(str(statement))
            return Result()

    class Operations:
        def get_bind(self):
            return Bind()

    monkeypatch.setattr(migration, "op", Operations())
    with pytest.raises(RuntimeError, match="does not resolve"):
        migration._resolve_target_table()
    online = statements[0]
    offline = migration._offline_upgrade_sql()
    for name, columns in migration._ANCHOR_SIGNATURES:
        expected = migration._anchor_predicate("x", columns)
        assert name in online and name in offline
        assert expected in online and expected in offline


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


@pytest.mark.parametrize("key_options", [(1, 0), (2, 0), (0, 1), (0, 2)])
def test_upload_ordering_index_migration_rejects_wrong_key_options(
    monkeypatch, key_options: tuple[int, int]
) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: _target(migration))
    monkeypatch.setattr(
        migration,
        "_find_existing_index",
        lambda actual_target: _expected_index(migration, key_options=key_options),
    )

    with pytest.raises(RuntimeError, match="unexpected definition"):
        migration.upgrade()

    assert operations.created_indexes == []
    assert operations.dropped_indexes == []


def test_downgrade_candidates_require_expected_key_options(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    expected = _expected_index(migration)
    wrong_order = _expected_index(migration, schema="shadow", key_options=(1, 0))
    monkeypatch.setattr(migration, "_index_rows", lambda *_args, **_kwargs: [expected, wrong_order])

    assert migration._downgrade_candidates() == [expected]


def test_downgrade_rejects_only_wrong_order_candidate(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(
        migration,
        "_index_rows",
        lambda *_args, **_kwargs: [_expected_index(migration, key_options=(2, 0))],
    )

    with pytest.raises(RuntimeError, match="no compatible managed index"):
        migration.downgrade()

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
    lookups = iter((None, _expected_index(migration), _expected_index(migration)))
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
