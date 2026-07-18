"""add global upload request ordering index

Revision ID: 0010_upload_created_index
Revises: 0009_db_integrity
"""

from dataclasses import dataclass

from sqlalchemy import text

from alembic import context, op

revision = "0010_upload_created_index"
down_revision = "0009_db_integrity"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_upload_requests_created_id"


@dataclass(frozen=True)
class _IndexSignature:
    schema: str
    table_oid: int
    table_schema: str
    table_name: str
    key_columns: tuple[str | None, ...]
    key_column_count: int
    total_column_count: int
    access_method: str
    is_unique: bool
    is_partial: bool
    is_expression: bool
    is_valid: bool
    is_ready: bool


@dataclass(frozen=True)
class _TargetTable:
    oid: int
    schema_oid: int
    schema: str
    name: str


def _is_offline_mode() -> bool:
    """Use Alembic's mode flag before issuing catalog queries."""
    return context.is_offline_mode()


def _resolve_target_table() -> _TargetTable:
    row = (
        op.get_bind()
        .execute(
            text("""
        SELECT table_class.oid AS oid, table_namespace.oid AS schema_oid,
               table_namespace.nspname AS schema, table_class.relname AS name
        FROM pg_class AS table_class
        JOIN pg_namespace AS table_namespace ON table_namespace.oid = table_class.relnamespace
        WHERE table_class.oid = to_regclass('upload_requests')
          AND table_class.relkind IN ('r', 'p')
    """)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise RuntimeError(
            "Cannot apply 0010_upload_created_index: upload_requests does not resolve "
            "to an ordinary or partitioned table."
        )
    return _TargetTable(row["oid"], row["schema_oid"], row["schema"], row["name"])


def _index_rows(where: str, parameters: dict[str, object] | None = None) -> list[_IndexSignature]:
    rows = (
        op.get_bind()
        .execute(
            text(f"""
        SELECT index_namespace.nspname AS schema, index_definition.indrelid AS table_oid,
               table_namespace.nspname AS table_schema, table_class.relname AS table_name,
               array_agg(attribute.attname ORDER BY key_attribute.ordinality)
                   FILTER (WHERE key_attribute.ordinality <= index_definition.indnkeyatts)
                   AS key_columns,
               max(index_definition.indnkeyatts) AS key_column_count,
               max(index_definition.indnatts) AS total_column_count, access_method.amname AS access_method,
               bool_or(index_definition.indisunique) AS is_unique,
               bool_or(index_definition.indpred IS NOT NULL) AS is_partial,
               bool_or(index_definition.indexprs IS NOT NULL) AS is_expression,
               bool_or(index_definition.indisvalid) AS is_valid, bool_or(index_definition.indisready) AS is_ready
        FROM pg_class AS index_class
        JOIN pg_namespace AS index_namespace ON index_namespace.oid = index_class.relnamespace
        JOIN pg_index AS index_definition ON index_definition.indexrelid = index_class.oid
        JOIN pg_class AS table_class ON table_class.oid = index_definition.indrelid
        JOIN pg_namespace AS table_namespace ON table_namespace.oid = table_class.relnamespace
        JOIN pg_am AS access_method ON access_method.oid = index_class.relam
        LEFT JOIN LATERAL unnest(index_definition.indkey)
            WITH ORDINALITY AS key_attribute(attnum, ordinality) ON TRUE
        LEFT JOIN pg_attribute AS attribute ON attribute.attrelid = index_definition.indrelid
            AND attribute.attnum = key_attribute.attnum
        WHERE index_class.relname = '{_INDEX_NAME}' AND {where}
        GROUP BY index_namespace.nspname, index_definition.indrelid, table_namespace.nspname,
                 table_class.relname, access_method.amname
    """),
            parameters or {},
        )
        .mappings()
        .all()
    )
    return [
        _IndexSignature(
            schema=row["schema"],
            table_oid=row["table_oid"],
            table_schema=row["table_schema"],
            table_name=row["table_name"],
            key_columns=tuple(row["key_columns"] or ()),
            key_column_count=row["key_column_count"],
            total_column_count=row["total_column_count"],
            access_method=row["access_method"],
            is_unique=row["is_unique"],
            is_partial=row["is_partial"],
            is_expression=row["is_expression"],
            is_valid=row["is_valid"],
            is_ready=row["is_ready"],
        )
        for row in rows
    ]


def _find_existing_index(target: _TargetTable) -> _IndexSignature | None:
    indexes = _index_rows("index_namespace.oid = :schema_oid", {"schema_oid": target.schema_oid})
    return indexes[0] if indexes else None


def _matches_expected_index(index: _IndexSignature, target: _TargetTable) -> bool:
    return (
        index.schema == target.schema
        and index.table_oid == target.oid
        and index.table_schema == target.schema
        and index.table_name == target.name
        and index.key_columns == ("created_at", "id")
        and index.key_column_count == 2
        and index.total_column_count == 2
        and index.access_method == "btree"
        and not index.is_unique
        and not index.is_partial
        and not index.is_expression
        and index.is_valid
        and index.is_ready
    )


def _validate_existing_index(index: _IndexSignature | None, target: _TargetTable) -> None:
    if index is None:
        raise RuntimeError(
            f"Cannot apply {revision}: index {_INDEX_NAME} was not found after creation."
        )
    if _matches_expected_index(index, target):
        return
    raise RuntimeError(
        f"Cannot apply {revision}: index {_INDEX_NAME} exists with an unexpected definition. "
        f"Expected table upload_requests with key columns (created_at, id); found table "
        f"{index.table_schema}.{index.table_name} with key columns {list(index.key_columns)!r}."
    )


def _downgrade_candidates() -> list[_IndexSignature]:
    return [
        index
        for index in _index_rows(
            "index_namespace.oid = table_namespace.oid "
            "AND table_class.relname = 'upload_requests' "
            "AND index_namespace.nspname NOT LIKE 'pg_%' "
            "AND index_namespace.nspname <> 'information_schema'"
        )
        if index.key_columns == ("created_at", "id")
        and index.key_column_count == 2
        and index.total_column_count == 2
        and index.access_method == "btree"
        and not index.is_unique
        and not index.is_partial
        and not index.is_expression
        and index.is_valid
        and index.is_ready
    ]


def _offline_upgrade_sql() -> str:
    return """
DO $$
DECLARE target_oid oid; target_schema text; named_count integer; valid_count integer;
BEGIN
  SELECT c.oid, n.nspname INTO target_oid, target_schema FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = to_regclass('upload_requests') AND c.relkind IN ('r', 'p');
  IF NOT FOUND THEN RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: target table upload_requests was not found'; END IF;
  SELECT count(*) INTO named_count FROM pg_class i JOIN pg_namespace n ON n.oid = i.relnamespace WHERE n.nspname = target_schema AND i.relname = 'ix_upload_requests_created_id';
  IF named_count = 0 THEN EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I.%I (created_at, id)', 'ix_upload_requests_created_id', target_schema, 'upload_requests'); END IF;
  SELECT count(*) INTO valid_count FROM pg_class i JOIN pg_namespace ins ON ins.oid = i.relnamespace JOIN pg_index x ON x.indexrelid = i.oid JOIN pg_class t ON t.oid = x.indrelid JOIN pg_namespace tns ON tns.oid = t.relnamespace JOIN pg_am am ON am.oid = i.relam WHERE i.relname = 'ix_upload_requests_created_id' AND ins.nspname = target_schema AND x.indrelid = target_oid AND ins.oid = tns.oid AND t.relname = 'upload_requests' AND x.indnkeyatts = 2 AND x.indnatts = 2 AND am.amname = 'btree' AND NOT x.indisunique AND x.indpred IS NULL AND x.indexprs IS NULL AND x.indisvalid AND x.indisready AND (SELECT array_agg(a.attname ORDER BY k.ordinality) FROM unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.attnum WHERE k.ordinality <= x.indnkeyatts) = ARRAY['created_at', 'id']::name[];
  IF valid_count = 0 THEN
    IF named_count = 0 THEN RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: index ix_upload_requests_created_id was not found after creation'; END IF;
    RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: index ix_upload_requests_created_id has an incompatible signature';
  END IF;
END $$;
"""


def _offline_downgrade_sql() -> str:
    return """
DO $$
DECLARE candidate_count integer; candidate_schema text; candidate_schemas text;
BEGIN
  SELECT count(*), min(ins.nspname), string_agg(format('%I', ins.nspname), ', ' ORDER BY ins.nspname) INTO candidate_count, candidate_schema, candidate_schemas FROM pg_class i JOIN pg_namespace ins ON ins.oid = i.relnamespace JOIN pg_index x ON x.indexrelid = i.oid JOIN pg_class t ON t.oid = x.indrelid JOIN pg_namespace tns ON tns.oid = t.relnamespace JOIN pg_am am ON am.oid = i.relam WHERE i.relname = 'ix_upload_requests_created_id' AND ins.oid = tns.oid AND t.relname = 'upload_requests' AND ins.nspname NOT LIKE 'pg_%' AND ins.nspname <> 'information_schema' AND x.indnkeyatts = 2 AND x.indnatts = 2 AND am.amname = 'btree' AND NOT x.indisunique AND x.indpred IS NULL AND x.indexprs IS NULL AND x.indisvalid AND x.indisready AND (SELECT array_agg(a.attname ORDER BY k.ordinality) FROM unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.attnum WHERE k.ordinality <= x.indnkeyatts) = ARRAY['created_at', 'id']::name[];
  IF candidate_count = 0 THEN RAISE EXCEPTION 'Cannot downgrade 0010_upload_created_index: no compatible managed index was found'; END IF;
  IF candidate_count > 1 THEN RAISE EXCEPTION 'Cannot downgrade 0010_upload_created_index: ambiguous compatible indexes in schemas: %', candidate_schemas; END IF;
  EXECUTE format('DROP INDEX %I.%I', candidate_schema, 'ix_upload_requests_created_id');
END $$;
"""


def upgrade() -> None:
    if _is_offline_mode():
        op.execute(_offline_upgrade_sql())
        return
    target = _resolve_target_table()
    existing = _find_existing_index(target)
    if existing is not None:
        _validate_existing_index(existing, target)
        return
    op.create_index(
        _INDEX_NAME, target.name, ["created_at", "id"], schema=target.schema, if_not_exists=True
    )
    _validate_existing_index(_find_existing_index(target), target)


def downgrade() -> None:
    if _is_offline_mode():
        op.execute(_offline_downgrade_sql())
        return
    candidates = _downgrade_candidates()
    if not candidates:
        raise RuntimeError(f"Cannot downgrade {revision}: no compatible managed index was found.")
    if len(candidates) > 1:
        schemas = ", ".join(repr(index.schema) for index in candidates)
        raise RuntimeError(
            f"Cannot downgrade {revision}: ambiguous compatible indexes in schemas: {schemas}."
        )
    candidate = candidates[0]
    op.drop_index(_INDEX_NAME, table_name=candidate.table_name, schema=candidate.schema)
