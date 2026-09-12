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


def test_anchor_opclasses_are_validated_from_ordered_catalog_oids() -> None:
    sql = _migration()._anchor_predicate("x", ("status", "created_at", "id"))

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


def _index(migration, **changes):  # noqa: ANN001, ANN003
    value = migration._IndexSignature(
        index_oid=84,
        index_schema_oid=2200,
        schema="public",
        table_oid=42,
        table_schema="public",
        table_name="upload_requests",
        table_kind="r",
        index_name="ix_upload_requests_created_id",
        key_columns=("created_at", "id"),
        key_options=(0, 0),
        key_opclasses=(3127, 1978),
        expected_key_opclasses=(3127, 1978),
        key_column_count=2,
        total_column_count=2,
        access_method="btree",
        is_unique=False,
        is_partial=False,
        is_expression=False,
        is_exclusion=False,
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


def test_rows_formats_sql_and_executes_it(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    calls = []

    class Result:
        def mappings(self):
            return self

        def all(self):
            return []

    class Bind:
        def execute(self, statement, parameters):  # noqa: ANN001
            calls.append((str(statement), parameters))
            return Result()

    monkeypatch.setattr(migration.op, "get_bind", lambda: Bind())

    assert migration._rows("i.oid = :index_oid", {"index_oid": 84}) == []
    assert len(calls) == 1
    assert "WHERE i.oid = :index_oid" in calls[0][0]
    assert calls[0][1] == {"index_oid": 84}


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
    assert "t.relkind::pg_catalog.text AS table_kind" in migration._INDEX_SELECT
    assert "t.relkind AS table_kind" not in migration._INDEX_SELECT


def test_owned_index_is_idempotent(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    operations = Operations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_owned_indexes", lambda: [_index(migration)])
    monkeypatch.setattr(migration, "_resolve_application_target", lambda: _target(migration))
    migration._online_upgrade()
    assert operations.executed == []


def test_null_comment_is_backfilled_and_post_validated(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    operations = Operations()
    candidate = _index(migration, ownership_comment=None)
    temporary = _index(migration, index_name="__yd_0011_adopt_84", ownership_comment=None)
    marked = replace(temporary, ownership_comment=migration._INDEX_OWNERSHIP_MARKER)
    final = _index(migration)
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_quote", lambda value: f'"{value}"')
    owned = iter(([], [], [marked], [final]))
    monkeypatch.setattr(migration, "_owned_indexes", lambda: next(owned))
    monkeypatch.setattr(migration, "_resolve_application_target", lambda: _target(migration))
    monkeypatch.setattr(migration, "_target_index", lambda _target: [candidate])
    rows = iter(([temporary], [marked], [final]))
    monkeypatch.setattr(migration, "_rows", lambda *_args, **_kwargs: next(rows))
    migration._online_upgrade()
    assert len(operations.executed) == 3
    assert 'COMMENT ON INDEX "public"."__yd_0011_adopt_84"' in operations.executed[1]
    assert str(operations.executed[0]).startswith("ALTER INDEX")
    assert "RENAME TO" in str(operations.executed[0])
    assert str(operations.executed[2]).endswith('RENAME TO "ix_upload_requests_created_id"')


def test_owner_committed_while_candidate_lock_waited_aborts_adoption(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    operations = Operations()
    candidate = _index(migration, ownership_comment=None)
    temporary = replace(candidate, index_name="__yd_0011_adopt_84")
    foreign = _index(migration, index_oid=85, ownership_comment=migration._INDEX_OWNERSHIP_MARKER)
    owned = iter(([], [foreign]))
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_quote", lambda value: f'"{value}"')
    monkeypatch.setattr(migration, "_owned_indexes", lambda: next(owned))
    monkeypatch.setattr(migration, "_resolve_application_target", lambda: _target(migration))
    monkeypatch.setattr(migration, "_target_index", lambda _target: [candidate])
    monkeypatch.setattr(migration, "_rows", lambda *_args, **_kwargs: [temporary])

    with pytest.raises(RuntimeError, match="ownership conflict"):
        migration._online_upgrade()

    assert len(operations.executed) == 1
    assert "RENAME TO" in operations.executed[0]


def test_additional_owner_after_comment_aborts_adoption(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    operations = Operations()
    candidate = _index(migration, ownership_comment=None)
    temporary = replace(candidate, index_name="__yd_0011_adopt_84")
    marked = replace(temporary, ownership_comment=migration._INDEX_OWNERSHIP_MARKER)
    foreign = _index(migration, index_oid=85)
    owned = iter(([], [], [marked, foreign]))
    rows = iter(([temporary], [marked]))
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_quote", lambda value: f'"{value}"')
    monkeypatch.setattr(migration, "_owned_indexes", lambda: next(owned))
    monkeypatch.setattr(migration, "_resolve_application_target", lambda: _target(migration))
    monkeypatch.setattr(migration, "_target_index", lambda _target: [candidate])
    monkeypatch.setattr(migration, "_rows", lambda *_args, **_kwargs: next(rows))

    with pytest.raises(RuntimeError, match="ambiguous owned indexes"):
        migration._online_upgrade()

    assert len(operations.executed) == 2


def test_adoption_owner_validation_accepts_temporary_name(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    temporary = _index(migration, index_name="__yd_0011_adopt_84")
    monkeypatch.setattr(migration, "_owned_indexes", lambda: [temporary])

    assert (
        migration._validate_adoption_ownership(
            84, _target(migration), expected_name="__yd_0011_adopt_84"
        )
        == temporary
    )


def test_adoption_owner_validation_rejects_different_oid(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    monkeypatch.setattr(migration, "_owned_indexes", lambda: [_index(migration, index_oid=85)])

    with pytest.raises(RuntimeError, match="ambiguous owned indexes"):
        migration._validate_adoption_ownership(
            84, _target(migration), expected_name=migration._INDEX_NAME
        )


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
        {"is_exclusion": True},
        {"key_opclasses": (1978, 3127)},
        {"key_opclasses": (9999, 1978)},
        {"table_oid": 99},
        {"index_schema_oid": 9999},
        {"is_valid": False},
        {"is_ready": False},
    ],
)
def test_full_signature_is_enforced(changes: dict[str, object]) -> None:
    migration = _migration()
    assert migration._matches(_index(migration, **changes), _target(migration)) is False


def test_expected_marker_on_incompatible_object_is_not_ignored(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    monkeypatch.setattr(migration, "_owned_indexes", lambda: [_index(migration, table_kind="m")])
    monkeypatch.setattr(migration, "_resolve_application_target", lambda: _target(migration))
    with pytest.raises(RuntimeError, match="incompatible index signature"):
        migration._online_upgrade()


def test_multiple_owned_indexes_are_ambiguous(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    monkeypatch.setattr(
        migration, "_owned_indexes", lambda: [_index(migration), _index(migration, index_oid=85)]
    )
    monkeypatch.setattr(migration, "_resolve_application_target", lambda: _target(migration))
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
    with pytest.raises(RuntimeError, match="locked index"):
        migration._online_upgrade()


def test_foreign_comment_seen_under_exact_index_lock_is_not_overwritten(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    operations = Operations()
    candidate = _index(migration, ownership_comment=None)
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "_quote", lambda value: f'"{value}"')
    monkeypatch.setattr(migration, "_owned_indexes", lambda: [])
    monkeypatch.setattr(migration, "_resolve_application_target", lambda: _target(migration))
    monkeypatch.setattr(migration, "_target_index", lambda _target: [candidate])
    monkeypatch.setattr(
        migration,
        "_rows",
        lambda *_args, **_kwargs: [
            _index(
                migration,
                index_name="__yd_0011_adopt_84",
                ownership_comment="foreign-owner",
            )
        ],
    )

    with pytest.raises(RuntimeError, match="locked index validation failure"):
        migration._online_upgrade()

    assert len(operations.executed) == 1
    assert str(operations.executed[0]).startswith("ALTER INDEX")


def test_downgrade_preserves_marker(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    operations = Operations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(migration, "_owned_indexes", lambda: [_index(migration)])
    monkeypatch.setattr(migration, "_resolve_application_target", lambda: _target(migration))
    migration.downgrade()
    assert operations.executed == []


def test_downgrade_requires_marker(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(migration, "_owned_indexes", lambda: [])
    monkeypatch.setattr(migration, "_resolve_application_target", lambda: _target(migration))
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
    migration = _migration()
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", *command], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    sql = result.stdout
    assert "ARRAY[0,0]::pg_catalog.int2[]" in sql
    assert "unnest(x.indclass)" in sql
    assert "pg_opclass" in sql and "opc.opcdefault" in sql
    assert "NOT x.indisexclusion" in sql
    assert "pg_description" not in sql
    assert "unnest(x.indoption)" in sql and "WITH ORDINALITY" in sql
    assert "t.relkind IN ('r','p')" in sql
    assert "pg_catalog.left(n.nspname,3) OPERATOR(pg_catalog.<>) 'pg_'" in sql
    assert "NOT LIKE 'pg_%'" not in sql
    assert "yd_upd_approver:alembic:0010_upload_created_index" in sql
    for name, columns in migration._ANCHOR_SIGNATURES:
        assert name in sql
        assert migration._anchor_predicate("x", columns) in sql
    if is_downgrade:
        assert "DROP INDEX" not in sql
        assert "managed index not found" in sql
    else:
        assert "ix_upload_requests_user_created_id" in sql
        assert "ix_upload_requests_status_created_id" in sql
        assert "COMMENT ON INDEX %I.%I IS %L" in sql
        assert "ALTER INDEX %I.%I RENAME TO %I" in sql
        assert "i.oid=index_oid" in sql
        assert "existing_comment IS NOT NULL" in sql
        assert "marker post-validation failure" in sql
        assert sql.count("INTO owned_count") >= 3
        assert "INTO owned_count,owned_oid" in sql
        assert "owned_oid OPERATOR(pg_catalog.<>) index_oid" in sql
