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
_EXPECTED_KEY_COLUMNS = ("created_at", "id")
_EXPECTED_KEY_OPTIONS = (0, 0)
_INDEX_OWNERSHIP_MARKER = "yd_upd_approver:alembic:0010_upload_created_index"
_ANCHOR_SIGNATURES = (
    ("ix_upload_requests_user_created_id", ("user_id", "created_at", "id")),
    ("ix_upload_requests_status_created_id", ("status", "created_at", "id")),
)


def _anchor_predicate(index_alias: str, columns: tuple[str, ...]) -> str:
    """Return the complete, search-path-independent anchor fingerprint."""
    column_array = ",".join(f"'{column}'" for column in columns)
    return f"""{index_alias}.indnkeyatts = 3 AND {index_alias}.indnatts = 3
      AND am.amname = 'btree' AND NOT {index_alias}.indisunique
      AND {index_alias}.indpred IS NULL AND {index_alias}.indexprs IS NULL
      AND NOT {index_alias}.indisexclusion
      AND {index_alias}.indisvalid AND {index_alias}.indisready
      AND (SELECT array_agg(a.attname ORDER BY k.ordinality)
           FROM unnest({index_alias}.indkey) WITH ORDINALITY k(attnum, ordinality)
           JOIN pg_attribute a ON a.attrelid={index_alias}.indrelid AND a.attnum=k.attnum
           WHERE k.ordinality <= {index_alias}.indnkeyatts)
          = ARRAY[{column_array}]::name[]
      AND (SELECT array_agg(o.option ORDER BY o.ordinality)
           FROM unnest({index_alias}.indoption) WITH ORDINALITY o(option, ordinality)
           WHERE o.ordinality <= {index_alias}.indnkeyatts)
          = ARRAY[0,0,0]::smallint[]
      AND (SELECT array_agg(ic.opclass_oid ORDER BY ic.ordinality)
           FROM unnest({index_alias}.indclass) WITH ORDINALITY ic(opclass_oid, ordinality)
           WHERE ic.ordinality <= {index_alias}.indnkeyatts)
          = (SELECT array_agg(opc.oid ORDER BY k.ordinality)
             FROM unnest({index_alias}.indkey) WITH ORDINALITY k(attnum, ordinality)
             JOIN pg_attribute a ON a.attrelid={index_alias}.indrelid AND a.attnum=k.attnum
             JOIN pg_opclass opc
               ON opc.opcmethod=(SELECT oid FROM pg_am WHERE amname='btree')
              AND opc.opcintype=a.atttypid AND opc.opcdefault
             WHERE k.ordinality <= {index_alias}.indnkeyatts)"""


def _anchor_exists(table_alias: str, name: str, columns: tuple[str, ...]) -> str:
    return f"""EXISTS (
            SELECT 1 FROM pg_class i JOIN pg_index x ON x.indexrelid=i.oid
            JOIN pg_am am ON am.oid=i.relam
            WHERE x.indrelid={table_alias}.oid AND i.relnamespace={table_alias}.relnamespace
              AND i.relname='{name}' AND {_anchor_predicate("x", columns)})"""


def _target_anchor_predicates(table_alias: str) -> str:
    return " AND ".join(
        _anchor_exists(table_alias, name, columns) for name, columns in _ANCHOR_SIGNATURES
    )


@dataclass(frozen=True)
class _IndexSignature:
    index_oid: int
    index_schema_oid: int
    schema: str
    table_oid: int
    table_schema: str
    table_name: str
    key_columns: tuple[str | None, ...]
    key_options: tuple[int, ...]
    key_opclasses: tuple[int, ...]
    expected_key_opclasses: tuple[int, ...]
    key_column_count: int
    total_column_count: int
    access_method: str
    is_unique: bool
    is_partial: bool
    is_expression: bool
    is_exclusion: bool
    is_valid: bool
    is_ready: bool
    ownership_comment: str | None


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
    anchors = _target_anchor_predicates("table_class")
    row = (
        op.get_bind()
        .execute(
            text(f"""
        SELECT table_class.oid AS oid, table_namespace.oid AS schema_oid,
               table_namespace.nspname AS schema, table_class.relname AS name
        FROM pg_class AS table_class
        JOIN pg_namespace AS table_namespace ON table_namespace.oid = table_class.relnamespace
        WHERE table_class.relname = 'upload_requests'
          AND table_class.relkind IN ('r', 'p')
          AND left(table_namespace.nspname, 3) <> 'pg_'
          AND table_namespace.nspname <> 'information_schema'
          AND {anchors}
    """)
        )
        .mappings()
        .all()
    )
    if not row:
        raise RuntimeError(
            "Cannot apply 0010_upload_created_index: upload_requests does not resolve "
            "to an ordinary or partitioned table."
        )
    if len(row) > 1:
        raise RuntimeError(
            "Cannot apply 0010_upload_created_index: application target is ambiguous."
        )
    found = row[0]
    return _TargetTable(found["oid"], found["schema_oid"], found["schema"], found["name"])


def _index_rows(
    where: str, parameters: dict[str, object] | None = None, *, require_name: bool = True
) -> list[_IndexSignature]:
    name_predicate = f"index_class.relname = '{_INDEX_NAME}' AND " if require_name else ""
    rows = (
        op.get_bind()
        .execute(
            text(f"""
        SELECT index_class.oid AS index_oid, index_namespace.oid AS index_schema_oid,
               index_namespace.nspname AS schema,
               index_definition.indrelid AS table_oid,
               table_namespace.nspname AS table_schema, table_class.relname AS table_name,
               array_agg(attribute.attname ORDER BY key_attribute.ordinality)
                   FILTER (WHERE key_attribute.ordinality <= index_definition.indnkeyatts)
                   AS key_columns,
               array_agg(key_option.option ORDER BY key_attribute.ordinality)
                   FILTER (WHERE key_attribute.ordinality <= index_definition.indnkeyatts)
                   AS key_options,
               array_agg(opclass.oid ORDER BY key_attribute.ordinality)
                   FILTER (WHERE key_attribute.ordinality <= index_definition.indnkeyatts)
                   AS key_opclasses,
               array_agg(default_opclass.oid ORDER BY key_attribute.ordinality)
                   FILTER (WHERE key_attribute.ordinality <= index_definition.indnkeyatts)
                   AS expected_key_opclasses,
               max(index_definition.indnkeyatts) AS key_column_count,
               max(index_definition.indnatts) AS total_column_count, access_method.amname AS access_method,
               bool_or(index_definition.indisunique) AS is_unique,
               bool_or(index_definition.indpred IS NOT NULL) AS is_partial,
               bool_or(index_definition.indexprs IS NOT NULL) AS is_expression,
               bool_or(index_definition.indisexclusion) AS is_exclusion,
               bool_or(index_definition.indisvalid) AS is_valid,
               bool_or(index_definition.indisready) AS is_ready,
               obj_description(index_class.oid, 'pg_class') AS ownership_comment
        FROM pg_class AS index_class
        JOIN pg_namespace AS index_namespace ON index_namespace.oid = index_class.relnamespace
        JOIN pg_index AS index_definition ON index_definition.indexrelid = index_class.oid
        JOIN pg_class AS table_class ON table_class.oid = index_definition.indrelid
        JOIN pg_namespace AS table_namespace ON table_namespace.oid = table_class.relnamespace
        JOIN pg_am AS access_method ON access_method.oid = index_class.relam
        LEFT JOIN LATERAL unnest(index_definition.indkey)
            WITH ORDINALITY AS key_attribute(attnum, ordinality) ON TRUE
        LEFT JOIN LATERAL unnest(index_definition.indoption)
            WITH ORDINALITY AS key_option(option, ordinality)
            ON key_option.ordinality = key_attribute.ordinality
        LEFT JOIN pg_attribute AS attribute ON attribute.attrelid = index_definition.indrelid
            AND attribute.attnum = key_attribute.attnum
        LEFT JOIN LATERAL unnest(index_definition.indclass)
            WITH ORDINALITY AS index_opclass(opclass_oid, ordinality)
            ON index_opclass.ordinality = key_attribute.ordinality
        LEFT JOIN pg_opclass AS opclass ON opclass.oid = index_opclass.opclass_oid
        LEFT JOIN pg_opclass AS default_opclass ON default_opclass.opcmethod = index_class.relam
            AND default_opclass.opcintype = attribute.atttypid AND default_opclass.opcdefault
        WHERE {name_predicate}{where}
        GROUP BY index_class.oid, index_namespace.oid, index_namespace.nspname,
                 index_definition.indrelid,
                 table_namespace.nspname, table_class.relname, access_method.amname
    """),
            parameters or {},
        )
        .mappings()
        .all()
    )
    return [
        _IndexSignature(
            index_oid=row["index_oid"],
            index_schema_oid=row["index_schema_oid"],
            schema=row["schema"],
            table_oid=row["table_oid"],
            table_schema=row["table_schema"],
            table_name=row["table_name"],
            key_columns=tuple(row["key_columns"] or ()),
            key_options=tuple(row["key_options"] or ()),
            key_opclasses=tuple(row["key_opclasses"] or ()),
            expected_key_opclasses=tuple(row["expected_key_opclasses"] or ()),
            key_column_count=row["key_column_count"],
            total_column_count=row["total_column_count"],
            access_method=row["access_method"],
            is_unique=row["is_unique"],
            is_partial=row["is_partial"],
            is_expression=row["is_expression"],
            is_exclusion=row["is_exclusion"],
            is_valid=row["is_valid"],
            is_ready=row["is_ready"],
            ownership_comment=row["ownership_comment"],
        )
        for row in rows
    ]


def _find_existing_index(target: _TargetTable) -> _IndexSignature | None:
    indexes = _index_rows("index_namespace.oid = :schema_oid", {"schema_oid": target.schema_oid})
    return indexes[0] if indexes else None


def _matches_expected_index(index: _IndexSignature, target: _TargetTable) -> bool:
    return (
        index.schema == target.schema
        and index.index_schema_oid == target.schema_oid
        and index.table_oid == target.oid
        and index.table_schema == target.schema
        and index.table_name == target.name
        and index.key_columns == _EXPECTED_KEY_COLUMNS
        and index.key_options == _EXPECTED_KEY_OPTIONS
        and index.key_opclasses == index.expected_key_opclasses
        and len(index.key_opclasses) == 2
        and index.key_column_count == 2
        and index.total_column_count == 2
        and index.access_method == "btree"
        and not index.is_unique
        and not index.is_partial
        and not index.is_expression
        and not index.is_exclusion
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
        f"Expected table upload_requests with key columns (created_at, id) and key options "
        f"{_EXPECTED_KEY_OPTIONS!r}; found table {index.table_schema}.{index.table_name} with "
        f"key columns {list(index.key_columns)!r} and key options {index.key_options!r}."
    )


def _downgrade_candidates() -> list[_IndexSignature]:
    return [
        index
        for index in _index_rows(
            "index_namespace.oid = table_namespace.oid "
            "AND table_class.relname = 'upload_requests' "
            "AND table_class.relkind IN ('r', 'p') "
            "AND left(index_namespace.nspname, 3) <> 'pg_' "
            "AND index_namespace.nspname <> 'information_schema'"
        )
        if index.key_columns == _EXPECTED_KEY_COLUMNS
        and index.key_options == _EXPECTED_KEY_OPTIONS
        and index.key_opclasses == index.expected_key_opclasses
        and index.key_column_count == 2
        and index.total_column_count == 2
        and index.access_method == "btree"
        and not index.is_unique
        and not index.is_partial
        and not index.is_expression
        and not index.is_exclusion
        and index.is_valid
        and index.is_ready
        and index.ownership_comment == _INDEX_OWNERSHIP_MARKER
    ]


def _quote_identifier(value: str) -> str:
    return op.get_bind().dialect.identifier_preparer.quote(value)


def _mark_owned_index(index: _IndexSignature, target: _TargetTable) -> None:
    # Installations that ran the original 0010 are brought to this invariant by
    # the forward revision 0011_upload_index_ownership.
    if index.ownership_comment not in (None, _INDEX_OWNERSHIP_MARKER):
        raise RuntimeError(
            f"Cannot apply {revision}: ownership conflict for index {_INDEX_NAME}; "
            "its existing comment belongs to another owner."
        )
    if index.ownership_comment is None:
        temporary_name = f"__yd_0010_adopt_{index.index_oid}"
        qualified_name = f"{_quote_identifier(index.schema)}.{_quote_identifier(_INDEX_NAME)}"
        temporary = f"{_quote_identifier(index.schema)}.{_quote_identifier(temporary_name)}"
        marker = _INDEX_OWNERSHIP_MARKER.replace("'", "''")
        op.execute(
            text(f"ALTER INDEX {qualified_name} RENAME TO {_quote_identifier(temporary_name)}")
        )
        locked_rows = _index_rows(
            "index_class.oid = :index_oid", {"index_oid": index.index_oid}, require_name=False
        )
        if (
            len(locked_rows) != 1
            or locked_rows[0].schema != target.schema
            or locked_rows[0].table_oid != target.oid
            or locked_rows[0].ownership_comment is not None
            or not _matches_expected_index(locked_rows[0], target)
        ):
            raise RuntimeError(f"Cannot apply {revision}: locked index validation failure.")
        op.execute(text(f"COMMENT ON INDEX {temporary} IS '{marker}'"))
        marked_rows = _index_rows(
            "index_class.oid = :index_oid", {"index_oid": index.index_oid}, require_name=False
        )
        if len(marked_rows) != 1 or marked_rows[0].ownership_comment != _INDEX_OWNERSHIP_MARKER:
            raise RuntimeError(f"Cannot apply {revision}: ownership marker was not stored.")
        op.execute(text(f"ALTER INDEX {temporary} RENAME TO {_quote_identifier(_INDEX_NAME)}"))
    marked = _find_existing_index(target)
    _validate_existing_index(marked, target)
    if marked is None or marked.ownership_comment != _INDEX_OWNERSHIP_MARKER:
        raise RuntimeError(
            f"Cannot apply {revision}: ownership marker was not stored for index {_INDEX_NAME}."
        )


def _offline_upgrade_sql() -> str:
    anchors = _target_anchor_predicates("c")
    return f"""
DO $$
DECLARE target_oid oid; target_schema text; index_oid oid; existing_comment text;
        named_count integer; valid_count integer; target_count integer; temporary_name text;
BEGIN
  SELECT count(*),min(c.oid),min(n.nspname) INTO target_count,target_oid,target_schema FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relname='upload_requests' AND c.relkind IN ('r','p') AND left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND {anchors};
  IF target_count=0 THEN RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: target table upload_requests was not found'; END IF;
  IF target_count>1 THEN RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: application target is ambiguous'; END IF;
  SELECT count(*) INTO named_count FROM pg_class i JOIN pg_namespace n ON n.oid = i.relnamespace WHERE n.nspname = target_schema AND i.relname = 'ix_upload_requests_created_id';
  IF named_count = 0 THEN EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I.%I (created_at, id)', 'ix_upload_requests_created_id', target_schema, 'upload_requests'); END IF;
  SELECT count(*) INTO valid_count FROM pg_class i JOIN pg_namespace ins ON ins.oid = i.relnamespace JOIN pg_index x ON x.indexrelid = i.oid JOIN pg_class t ON t.oid = x.indrelid JOIN pg_namespace tns ON tns.oid = t.relnamespace JOIN pg_am am ON am.oid = i.relam WHERE i.relname = 'ix_upload_requests_created_id' AND ins.nspname = target_schema AND x.indrelid = target_oid AND ins.oid = tns.oid AND t.relname = 'upload_requests' AND x.indnkeyatts = 2 AND x.indnatts = 2 AND am.amname = 'btree' AND NOT x.indisunique AND NOT x.indisexclusion AND x.indpred IS NULL AND x.indexprs IS NULL AND x.indisvalid AND x.indisready AND (SELECT array_agg(a.attname ORDER BY k.ordinality) FROM unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.attnum WHERE k.ordinality <= x.indnkeyatts) = ARRAY['created_at', 'id']::name[] AND (SELECT array_agg(o.option ORDER BY o.ordinality) FROM unnest(x.indoption) WITH ORDINALITY AS o(option, ordinality) WHERE o.ordinality <= x.indnkeyatts) = ARRAY[0, 0]::smallint[] AND (SELECT array_agg(ic.opclass_oid ORDER BY ic.ordinality) FROM unnest(x.indclass) WITH ORDINALITY AS ic(opclass_oid, ordinality) WHERE ic.ordinality <= x.indnkeyatts) = (SELECT array_agg(opc.oid ORDER BY k.ordinality) FROM unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_attribute a ON a.attrelid=x.indrelid AND a.attnum=k.attnum JOIN pg_opclass opc ON opc.opcmethod=(SELECT oid FROM pg_am WHERE amname='btree') AND opc.opcintype=a.atttypid AND opc.opcdefault WHERE k.ordinality <= x.indnkeyatts);
  IF valid_count = 0 THEN
    IF named_count = 0 THEN RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: index ix_upload_requests_created_id was not found after creation'; END IF;
    RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: index ix_upload_requests_created_id has an incompatible signature';
  END IF;
  SELECT i.oid, obj_description(i.oid, 'pg_class') INTO STRICT index_oid, existing_comment
    FROM pg_class i JOIN pg_namespace n ON n.oid = i.relnamespace
    WHERE n.nspname = target_schema AND i.relname = 'ix_upload_requests_created_id';
  IF existing_comment IS NOT NULL AND existing_comment <> '{_INDEX_OWNERSHIP_MARKER}' THEN
    RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: ownership conflict for index ix_upload_requests_created_id; its existing comment belongs to another owner';
  END IF;
  IF existing_comment IS NULL THEN
    temporary_name := '__yd_0010_adopt_' || index_oid::text;
    EXECUTE format('ALTER INDEX %I.%I RENAME TO %I', target_schema,
                   'ix_upload_requests_created_id', temporary_name);
    SELECT count(*) INTO valid_count FROM pg_class i JOIN pg_namespace ins ON ins.oid=i.relnamespace JOIN pg_index x ON x.indexrelid=i.oid JOIN pg_class t ON t.oid=x.indrelid JOIN pg_namespace tns ON tns.oid=t.relnamespace JOIN pg_am am ON am.oid=i.relam WHERE i.oid=index_oid AND ins.nspname=target_schema AND x.indrelid=target_oid AND ins.oid=tns.oid AND t.relname='upload_requests' AND obj_description(i.oid,'pg_class') IS NULL AND x.indnkeyatts=2 AND x.indnatts=2 AND am.amname='btree' AND NOT x.indisunique AND NOT x.indisexclusion AND x.indpred IS NULL AND x.indexprs IS NULL AND x.indisvalid AND x.indisready AND (SELECT array_agg(a.attname ORDER BY k.ordinality) FROM unnest(x.indkey) WITH ORDINALITY k(attnum,ordinality) JOIN pg_attribute a ON a.attrelid=x.indrelid AND a.attnum=k.attnum WHERE k.ordinality<=x.indnkeyatts)=ARRAY['created_at','id']::name[] AND (SELECT array_agg(o.option ORDER BY o.ordinality) FROM unnest(x.indoption) WITH ORDINALITY o(option,ordinality) WHERE o.ordinality<=x.indnkeyatts)=ARRAY[0,0]::smallint[] AND (SELECT array_agg(ic.opclass_oid ORDER BY ic.ordinality) FROM unnest(x.indclass) WITH ORDINALITY ic(opclass_oid,ordinality) WHERE ic.ordinality<=x.indnkeyatts)=(SELECT array_agg(opc.oid ORDER BY k.ordinality) FROM unnest(x.indkey) WITH ORDINALITY k(attnum,ordinality) JOIN pg_attribute a ON a.attrelid=x.indrelid AND a.attnum=k.attnum JOIN pg_opclass opc ON opc.opcmethod=(SELECT oid FROM pg_am WHERE amname='btree') AND opc.opcintype=a.atttypid AND opc.opcdefault WHERE k.ordinality<=x.indnkeyatts);
    IF valid_count <> 1 THEN RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: locked index validation failure'; END IF;
    EXECUTE format('COMMENT ON INDEX %I.%I IS %L', target_schema,
                   temporary_name, '{_INDEX_OWNERSHIP_MARKER}');
    EXECUTE format('ALTER INDEX %I.%I RENAME TO %I', target_schema,
                   temporary_name, 'ix_upload_requests_created_id');
  END IF;
  IF obj_description(index_oid, 'pg_class') IS DISTINCT FROM '{_INDEX_OWNERSHIP_MARKER}' THEN
    RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: ownership marker was not stored for index ix_upload_requests_created_id';
  END IF;
END $$;
"""


def _offline_downgrade_sql() -> str:
    return """
DO $$
DECLARE candidate_count integer; candidate_schema text; candidate_schemas text;
BEGIN
  SELECT count(*), min(ins.nspname), string_agg(format('%I', ins.nspname), ', ' ORDER BY ins.nspname) INTO candidate_count, candidate_schema, candidate_schemas FROM pg_class i JOIN pg_namespace ins ON ins.oid = i.relnamespace JOIN pg_index x ON x.indexrelid = i.oid JOIN pg_class t ON t.oid = x.indrelid JOIN pg_namespace tns ON tns.oid = t.relnamespace JOIN pg_am am ON am.oid = i.relam WHERE i.relname = 'ix_upload_requests_created_id' AND ins.oid = tns.oid AND t.relname = 'upload_requests' AND t.relkind IN ('r', 'p') AND left(ins.nspname, 3) <> 'pg_' AND ins.nspname <> 'information_schema' AND obj_description(i.oid, 'pg_class') = 'yd_upd_approver:alembic:0010_upload_created_index' AND x.indnkeyatts = 2 AND x.indnatts = 2 AND am.amname = 'btree' AND NOT x.indisunique AND NOT x.indisexclusion AND x.indpred IS NULL AND x.indexprs IS NULL AND x.indisvalid AND x.indisready AND (SELECT array_agg(a.attname ORDER BY k.ordinality) FROM unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.attnum WHERE k.ordinality <= x.indnkeyatts) = ARRAY['created_at', 'id']::name[] AND (SELECT array_agg(o.option ORDER BY o.ordinality) FROM unnest(x.indoption) WITH ORDINALITY AS o(option, ordinality) WHERE o.ordinality <= x.indnkeyatts) = ARRAY[0, 0]::smallint[] AND (SELECT array_agg(ic.opclass_oid ORDER BY ic.ordinality) FROM unnest(x.indclass) WITH ORDINALITY AS ic(opclass_oid, ordinality) WHERE ic.ordinality <= x.indnkeyatts) = (SELECT array_agg(opc.oid ORDER BY k.ordinality) FROM unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_attribute a ON a.attrelid=x.indrelid AND a.attnum=k.attnum JOIN pg_opclass opc ON opc.opcmethod=(SELECT oid FROM pg_am WHERE amname='btree') AND opc.opcintype=a.atttypid AND opc.opcdefault WHERE k.ordinality <= x.indnkeyatts);
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
        _mark_owned_index(existing, target)
        return
    op.create_index(
        _INDEX_NAME, target.name, ["created_at", "id"], schema=target.schema, if_not_exists=True
    )
    created = _find_existing_index(target)
    _validate_existing_index(created, target)
    assert created is not None
    _mark_owned_index(created, target)


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
