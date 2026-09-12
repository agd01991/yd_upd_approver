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
    block_start = manual_qa.index("<!-- upload-index-managed-signature-sql:start -->")
    block_end = manual_qa.index("<!-- upload-index-managed-signature-sql:end -->", block_start)
    query_start = manual_qa.index("```sql", block_start, block_end) + len("```sql")
    query_end = manual_qa.index("```", query_start, block_end)
    query = manual_qa[query_start:query_end]

    assert "REPLACE_WITH_APPLICATION_SCHEMA" in query
    assert "to_regclass('upload_requests')" not in query
    for relation in ("pg_namespace", "pg_class", "pg_index", "pg_attribute", "pg_opclass"):
        assert not re.search(rf"\bJOIN\s+(?!pg_catalog\.){relation}\b", query, re.IGNORECASE)
    for function in ("array_agg", "unnest", "pg_get_indexdef", "obj_description"):
        assert not re.search(rf"(?<!pg_catalog\.)\b{function}\s*\(", query, re.IGNORECASE)
    assert "AS qa_pass" in query
    assert "actual_opclasses OPERATOR(pg_catalog.=) default_opclasses" in query
    assert "joined_key_count OPERATOR(pg_catalog.=) 2" in query
    assert "ARRAY['created_at','id']::pg_catalog.text[]" in query
    assert "ARRAY[0,0]::pg_catalog.int2[]" in query
    assert "yd_upd_approver:alembic:0010_upload_created_index" in query


@pytest.fixture(autouse=True)
def _use_online_migration_mode(monkeypatch) -> None:  # noqa: ANN001
    """Unit tests exercise the online branch without an Alembic EnvironmentContext."""
    migration = _migration_module()
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: False)


def test_ownership_protocol_lock_is_stable_and_database_local() -> None:
    migration = _migration_module()
    backfill = (
        ScriptDirectory.from_config(Config("alembic.ini"))
        .get_revision("0011_upload_index_ownership")
        .module
    )
    assert migration._OWNERSHIP_LOCK_KEY == backfill._OWNERSHIP_LOCK_KEY
    sql = migration._offline_ownership_lock_sql()
    assert "pg_catalog.pg_advisory_xact_lock" in sql
    assert f"{migration._OWNERSHIP_LOCK_KEY}::pg_catalog.int8" in sql
    assert "READ COMMITTED" in sql


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
        if "pg_advisory_xact_lock" not in str(statement):
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
    assert "LEFT JOIN pg_catalog.pg_opclass opc ON opc.oid = ic.opclass_oid" in sql
    assert "opc.opcmethod = am.oid AND opc.opcdefault" in sql
    assert "opc.opcintype = a.atttypid" in sql
    assert "typ.typtype = 'e'" in sql
    assert "opc.opcintype = 'pg_catalog.anyenum'::pg_catalog.regtype" in sql
    assert "count(*) = x.indnkeyatts" in sql
    assert "COALESCE(pg_catalog.bool_and" in sql
    assert "enum_ops" not in sql
    assert "array_agg(a.atttypid ORDER BY k.ordinality)" in sql
    assert "'pg_catalog.int4'::pg_catalog.regtype::pg_catalog.oid" in sql
    assert "'pg_catalog.timestamptz'::pg_catalog.regtype::pg_catalog.oid" in sql
    assert "typ.typname = 'uploadstatus'" in sql
    assert "pg_enum enum" in sql and "enum.enumsortorder" in sql
    assert "array_agg(enum.enumlabel::pg_catalog.text ORDER BY enum.enumsortorder)" in sql
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
    assert "array_agg(enum.enumlabel::pg_catalog.text ORDER BY enum.enumsortorder)" in status_sql


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
    candidate = _expected_index(migration)
    monkeypatch.setattr(migration, "_downgrade_candidates", lambda: [candidate])
    owners = iter(([], [84], [84], [84], [84]))
    monkeypatch.setattr(migration, "_owned_index_oids", lambda: next(owners))
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: target)
    monkeypatch.setattr(migration, "_quote_identifier", lambda value: value)
    monkeypatch.setattr(
        migration,
        "_index_rows",
        lambda *args, **kwargs: [_expected_index(migration, index_name="__yd_0010_adopt_84")],
    )

    migration.upgrade()
    monkeypatch.setattr(
        migration,
        "_index_rows",
        lambda *_args, **_kwargs: [_expected_index(migration, index_name="__yd_0010_drop_84")],
    )
    migration.downgrade()

    assert operations.created_indexes == [
        (
            "ix_upload_requests_created_id",
            "upload_requests",
            ["created_at", "id"],
            {"schema": "public", "if_not_exists": True},
        )
    ]
    assert operations.dropped_indexes == []
    assert any(
        "ALTER INDEX public.ix_upload_requests_created_id RENAME TO" in str(sql)
        for sql in operations.executed
    )
    assert any("DROP INDEX public.__yd_0010_drop_" in str(sql) for sql in operations.executed)


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
    assert "ARRAY[0, 0]::pg_catalog.int2[]" in result.stdout
    if command[0] == "downgrade":
        assert "t.relkind IN ('r', 'p')" in result.stdout
        assert "pg_catalog.left(ins.nspname, 3)  OPERATOR(pg_catalog.<>)  'pg_'" in result.stdout
        assert "NOT LIKE 'pg_%'" not in result.stdout
        assert (
            "pg_catalog.obj_description(i.oid, 'pg_class') = "
            "'yd_upd_approver:alembic:0010_upload_created_index'"
        ) in result.stdout
    else:
        for columns in (
            "ARRAY['user_id','created_at','id']::pg_catalog.name[]",
            "ARRAY['status','created_at','id']::pg_catalog.name[]",
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
            "ARRAY[0,0,0]::pg_catalog.int2[]",
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
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: _target(migration))
    monkeypatch.setattr(
        migration,
        "_downgrade_candidates",
        lambda: [_expected_index(migration), _expected_index(migration, schema="other")],
    )

    with pytest.raises(RuntimeError, match="duplicate ownership markers"):
        migration.downgrade()

    assert operations.dropped_indexes == []


def test_upload_ordering_index_downgrade_rejects_missing_candidate(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: _target(migration))
    monkeypatch.setattr(migration, "_downgrade_candidates", lambda: [])

    with pytest.raises(RuntimeError, match="ownership marker was not found"):
        migration.downgrade()

    assert operations.dropped_indexes == []


def test_downgrade_candidates_load_every_marker_owner_before_validation(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    expected = _expected_index(migration)
    wrong_name_and_order = _expected_index(
        migration, index_oid=85, index_name="foreign_marker", schema="other", key_options=(1, 0)
    )
    monkeypatch.setattr(
        migration, "_owned_index_oids", lambda: [expected.index_oid, wrong_name_and_order.index_oid]
    )
    rows = {expected.index_oid: [expected], wrong_name_and_order.index_oid: [wrong_name_and_order]}
    monkeypatch.setattr(
        migration,
        "_index_rows",
        lambda _where, parameters, **_kwargs: rows[parameters["index_oid"]],
    )

    assert migration._downgrade_candidates() == [expected, wrong_name_and_order]


@pytest.mark.parametrize("target_state", ["absent", "unmarked", "owned"])
@pytest.mark.parametrize("foreign_oids", [[85], [85, 86]])
def test_upgrade_rejects_global_marker_collisions_before_mutation(
    monkeypatch, target_state: str, foreign_oids: list[int]
) -> None:
    migration = _migration_module()
    operations = RecordingOperations()
    existing = (
        None
        if target_state == "absent"
        else _expected_index(
            migration,
            ownership_comment=migration._INDEX_OWNERSHIP_MARKER
            if target_state == "owned"
            else None,
        )
    )
    owners = ([84] if target_state == "owned" else []) + foreign_oids
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: _target(migration))
    monkeypatch.setattr(migration, "_find_existing_index", lambda _target: existing)
    monkeypatch.setattr(migration, "_owned_index_oids", lambda: owners)
    adopted: list[int] = []
    monkeypatch.setattr(
        migration, "_mark_owned_index", lambda index, _target: adopted.append(index.index_oid)
    )

    with pytest.raises(RuntimeError, match="ownership (conflict|markers)"):
        migration.upgrade()

    assert operations.created_indexes == []
    assert operations.executed == []
    assert adopted == []


@pytest.mark.parametrize("phase", ["after-lock", "after-comment"])
def test_upgrade_rechecks_global_owners_during_adoption(monkeypatch, phase: str) -> None:
    migration = _migration_module()
    operations = RecordingOperations()
    unowned = _expected_index(migration, ownership_comment=None)
    locked = _expected_index(migration, index_name="__yd_0010_adopt_84", ownership_comment=None)
    rows = iter(([locked], [_expected_index(migration, index_name="__yd_0010_adopt_84")]))
    owners = iter(([], [85]) if phase == "after-lock" else ([], [], [84, 85]))
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_quote_identifier", lambda value: value)
    monkeypatch.setattr(migration, "_owned_index_oids", lambda: next(owners))
    monkeypatch.setattr(migration, "_index_rows", lambda *_args, **_kwargs: next(rows))
    monkeypatch.setattr(
        migration, "_find_existing_index", lambda _target: _expected_index(migration)
    )

    with pytest.raises(RuntimeError, match="ownership (conflict|markers)"):
        migration._mark_owned_index(unowned, _target(migration))

    assert len(operations.executed) == (1 if phase == "after-lock" else 3)
    if phase == "after-lock":
        assert not any("COMMENT ON INDEX" in str(sql) for sql in operations.executed)


def test_offline_upgrade_counts_the_same_unfiltered_owners_at_each_stage() -> None:
    migration = _migration_module()
    inventory = migration._owned_index_oids_sql()
    assert "pg_catalog.pg_class" in inventory and "pg_catalog.pg_index" in inventory
    assert "OPERATOR(pg_catalog.=)" in inventory
    for filtered_field in ("relname", "relnamespace", "indrelid", "indisvalid", "indkey"):
        assert filtered_field not in inventory
    sql = migration._offline_upgrade_sql()
    guard = migration._offline_upgrade_owners_sql()
    assert inventory in guard
    assert sql.index(guard) < sql.index("CREATE INDEX IF NOT EXISTS")
    rename = sql.index("ALTER INDEX %I.%I RENAME TO %I")
    assert rename < sql.index(guard, rename) < sql.index("COMMENT ON INDEX")
    assert migration._offline_upgrade_owners_sql(require_owned=True) in sql


def test_upgrade_adopts_uncommented_compatible_index(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    owners = iter(([], [], [], [84]))
    monkeypatch.setattr(migration, "_owned_index_oids", lambda: next(owners))
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_quote_identifier", lambda value: f'"{value}"')
    target = _target(migration)
    unowned = _expected_index(migration, ownership_comment=None)
    lookups = iter((unowned, _expected_index(migration)))
    locked = _expected_index(migration, index_name="__yd_0010_adopt_84", ownership_comment=None)
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


@pytest.mark.parametrize("already_owned", [False, True])
def test_upgrade_rejects_rename_replacement_while_original_oid_survives(
    monkeypatch, already_owned: bool
) -> None:
    migration = _migration_module()
    comment = migration._INDEX_OWNERSHIP_MARKER if already_owned else None
    monkeypatch.setattr(migration, "_owned_index_oids", lambda: [84] if already_owned else [])
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_quote_identifier", lambda value: f'"{value}"')
    target = _target(migration)
    original = _expected_index(migration, ownership_comment=comment)
    # The selected OID still has the complete expected signature and comment state,
    # but a concurrent transaction renamed it elsewhere and put B under the old name.
    surviving_a = _expected_index(
        migration, index_name="concurrent_saved_a", ownership_comment=comment
    )
    monkeypatch.setattr(migration, "_index_rows", lambda *_args, **_kwargs: [surviving_a])

    with pytest.raises(RuntimeError, match="locked index validation failure"):
        migration._mark_owned_index(original, target)

    assert len(operations.executed) == 1
    assert "COMMENT ON INDEX" not in str(operations.executed[0])


def test_downgrade_rejects_rename_replacement_while_original_oid_survives(monkeypatch) -> None:
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_quote_identifier", lambda value: f'"{value}"')
    candidate = _expected_index(migration)
    surviving_a = _expected_index(migration, index_name="concurrent_saved_a")
    monkeypatch.setattr(migration, "_downgrade_candidates", lambda: [candidate])
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: _target(migration))
    monkeypatch.setattr(migration, "_index_rows", lambda *_args, **_kwargs: [surviving_a])
    monkeypatch.setattr(migration, "_owned_index_oids", lambda: [candidate.index_oid])

    with pytest.raises(RuntimeError, match="locked index validation failure"):
        migration.downgrade()

    assert len(operations.executed) == 1
    assert "DROP INDEX" not in str(operations.executed[0])


def test_upgrade_refuses_foreign_comment_without_overwriting_it(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    monkeypatch.setattr(migration, "_owned_index_oids", lambda: [])
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
    owners = iter(([], [], [], [84]))
    monkeypatch.setattr(migration, "_owned_index_oids", lambda: next(owners))
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_quote_identifier", lambda value: f'"{value}"')
    target = _target(migration)
    unowned = _expected_index(migration, ownership_comment=None)
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: target)
    monkeypatch.setattr(migration, "_find_existing_index", lambda _target: unowned)
    locked = _expected_index(migration, index_name="__yd_0010_adopt_84", ownership_comment=None)
    oid_lookups = iter(([locked], [locked]))
    monkeypatch.setattr(migration, "_index_rows", lambda *_args, **_kwargs: next(oid_lookups))

    with pytest.raises(RuntimeError, match="ownership marker was not stored"):
        migration.upgrade()

    assert len(operations.executed) == 2


def test_upload_ordering_index_migration_accepts_concurrently_created_index(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    owners = iter(([], [84], [84], [84]))
    monkeypatch.setattr(migration, "_owned_index_oids", lambda: next(owners))
    monkeypatch.setattr(migration, "_quote_identifier", lambda value: value)
    monkeypatch.setattr(
        migration,
        "_index_rows",
        lambda *_args, **_kwargs: [_expected_index(migration, index_name="__yd_0010_adopt_84")],
    )
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
    monkeypatch.setattr(migration, "_owned_index_oids", lambda: [])
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
    monkeypatch.setattr(migration, "_owned_index_oids", lambda: [])
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
        "index_name": "ix_upload_requests_created_id",
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
    offline_upgrade = migration._offline_upgrade_sql()
    offline_downgrade = migration._offline_downgrade_sql()
    for name, columns in migration._ANCHOR_SIGNATURES:
        expected = migration._anchor_predicate("x", columns)
        assert name in online and name in offline_upgrade and name in offline_downgrade
        assert expected in online and expected in offline_upgrade and expected in offline_downgrade


def test_upload_ordering_index_migration_accepts_correct_intermediate_index(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    owners = iter(([84], [84], [84], [84]))
    monkeypatch.setattr(migration, "_owned_index_oids", lambda: next(owners))
    monkeypatch.setattr(migration, "_quote_identifier", lambda value: value)
    monkeypatch.setattr(
        migration,
        "_index_rows",
        lambda *_args, **_kwargs: [_expected_index(migration, index_name="__yd_0010_adopt_84")],
    )
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


def test_downgrade_rejects_single_marker_with_wrong_order_without_mutation(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration_module()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_resolve_target_table", lambda: _target(migration))
    monkeypatch.setattr(
        migration,
        "_downgrade_candidates",
        lambda: [_expected_index(migration, key_options=(2, 0))],
    )

    with pytest.raises(RuntimeError, match="incompatible managed index target"):
        migration.downgrade()

    assert operations.executed == []
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
    owners = iter(([], [84], [84], [84]))
    monkeypatch.setattr(migration, "_owned_index_oids", lambda: next(owners))
    monkeypatch.setattr(migration, "_quote_identifier", lambda value: value)
    monkeypatch.setattr(
        migration,
        "_index_rows",
        lambda *_args, **_kwargs: [_expected_index(migration, index_name="__yd_0010_adopt_84")],
    )
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
