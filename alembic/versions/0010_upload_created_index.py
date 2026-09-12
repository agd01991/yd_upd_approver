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
_OWNERSHIP_LOCK_KEY = 780984123042210011
_ANCHOR_SIGNATURES = (
    ("ix_upload_requests_user_created_id", ("user_id", "created_at", "id")),
    ("ix_upload_requests_status_created_id", ("status", "created_at", "id")),
)

_UPLOAD_STATUS_LABELS = (
    "new",
    "stored",
    "pending_approval",
    "approved",
    "uploading",
    "uploaded",
    "rejected",
    "failed",
    "cancelled",
    "deleted_temp",
)


def _expected_anchor_type_oids(columns: tuple[str, ...]) -> str:
    """Build expected type OIDs without consulting the candidate table.

    The schema invariant established by 0001 is one catalog enum named
    ``uploadstatus`` with exactly these labels.  Zero matches rejects every target;
    multiple matches make the scalar subquery fail.  Thus schema/search_path
    duplicates fail closed rather than selecting an arbitrary enum OID.
    """
    labels = ",".join(f"'{label}'" for label in _UPLOAD_STATUS_LABELS)
    upload_status_oid = f"""(SELECT typ.oid
             FROM pg_catalog.pg_type typ
             JOIN pg_catalog.pg_namespace type_ns ON type_ns.oid = typ.typnamespace
             WHERE typ.typname = 'uploadstatus' AND typ.typtype = 'e'
               AND pg_catalog.left(type_ns.nspname, 3)  OPERATOR(pg_catalog.<>)  'pg_'
               AND type_ns.nspname  OPERATOR(pg_catalog.<>)  'information_schema'
               AND (SELECT pg_catalog.array_agg(enum.enumlabel::pg_catalog.text ORDER BY enum.enumsortorder)
                    FROM pg_catalog.pg_enum enum WHERE enum.enumtypid = typ.oid)
                   = ARRAY[{labels}]::pg_catalog.text[])"""
    expected = {
        "user_id": "'pg_catalog.int4'::pg_catalog.regtype::pg_catalog.oid",
        "status": upload_status_oid,
        "created_at": "'pg_catalog.timestamptz'::pg_catalog.regtype::pg_catalog.oid",
        "id": "'pg_catalog.int4'::pg_catalog.regtype::pg_catalog.oid",
    }
    return "ARRAY[" + ",".join(expected[column] for column in columns) + "]::pg_catalog.oid[]"


def _anchor_predicate(index_alias: str, columns: tuple[str, ...]) -> str:
    """Return the complete, search-path-independent anchor fingerprint."""
    column_array = ",".join(f"'{column}'" for column in columns)
    return f"""{index_alias}.indnkeyatts = 3 AND {index_alias}.indnatts = 3
      AND am.amname = 'btree' AND NOT {index_alias}.indisunique
      AND {index_alias}.indpred IS NULL AND {index_alias}.indexprs IS NULL
      AND NOT {index_alias}.indisexclusion
      AND {index_alias}.indisvalid AND {index_alias}.indisready
      AND (SELECT pg_catalog.array_agg(a.attname ORDER BY k.ordinality)
           FROM pg_catalog.unnest({index_alias}.indkey) WITH ORDINALITY k(attnum, ordinality)
           JOIN pg_catalog.pg_attribute a ON a.attrelid={index_alias}.indrelid AND a.attnum=k.attnum
           WHERE k.ordinality <= {index_alias}.indnkeyatts)
          = ARRAY[{column_array}]::pg_catalog.name[]
      AND (SELECT pg_catalog.array_agg(a.atttypid ORDER BY k.ordinality)
           FROM pg_catalog.unnest({index_alias}.indkey) WITH ORDINALITY k(attnum, ordinality)
           JOIN pg_catalog.pg_attribute a ON a.attrelid={index_alias}.indrelid AND a.attnum=k.attnum
           WHERE k.ordinality <= {index_alias}.indnkeyatts)
          = {_expected_anchor_type_oids(columns)}
      AND (SELECT pg_catalog.array_agg(o.option ORDER BY o.ordinality)
           FROM pg_catalog.unnest({index_alias}.indoption) WITH ORDINALITY o(option, ordinality)
           WHERE o.ordinality <= {index_alias}.indnkeyatts)
          = ARRAY[0,0,0]::pg_catalog.int2[]
      AND (SELECT pg_catalog.count(*) = {index_alias}.indnkeyatts
                  AND COALESCE(pg_catalog.bool_and((
                        ic.opclass_oid IS NOT NULL AND a.attnum IS NOT NULL
                        AND typ.oid IS NOT NULL AND opc.oid IS NOT NULL
                        AND opc.opcmethod = am.oid AND opc.opcdefault
                        AND (opc.opcintype = a.atttypid OR
                             (typ.typtype = 'e' AND
                              opc.opcintype = 'pg_catalog.anyenum'::pg_catalog.regtype))
                      ) IS TRUE), false)
           FROM pg_catalog.unnest({index_alias}.indkey) WITH ORDINALITY k(attnum, ordinality)
           LEFT JOIN pg_catalog.unnest({index_alias}.indclass) WITH ORDINALITY
             ic(opclass_oid, ordinality) ON ic.ordinality = k.ordinality
           LEFT JOIN pg_catalog.pg_attribute a
             ON a.attrelid={index_alias}.indrelid AND a.attnum=k.attnum
           LEFT JOIN pg_catalog.pg_type typ ON typ.oid = a.atttypid
           LEFT JOIN pg_catalog.pg_opclass opc ON opc.oid = ic.opclass_oid
           WHERE k.ordinality <= {index_alias}.indnkeyatts)"""


def _anchor_exists(table_alias: str, name: str, columns: tuple[str, ...]) -> str:
    return f"""EXISTS (
            SELECT 1 FROM pg_catalog.pg_class i JOIN pg_catalog.pg_index x ON x.indexrelid=i.oid
            JOIN pg_catalog.pg_am am ON am.oid=i.relam
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
    index_name: str
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


def _acquire_ownership_lock() -> None:
    """Enter the cooperative marker protocol before its first catalog read."""
    op.execute(text(f"DO $$ BEGIN {_offline_ownership_lock_sql()} END $$"))


def _offline_ownership_lock_sql() -> str:
    return f"""
  IF pg_catalog.current_setting('transaction_isolation') OPERATOR(pg_catalog.<>)
     'read committed'::pg_catalog.text THEN
    RAISE EXCEPTION 'Cannot apply {revision}: ownership protocol requires READ COMMITTED isolation';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock({_OWNERSHIP_LOCK_KEY}::pg_catalog.int8);
"""


def _resolve_target_table() -> _TargetTable:
    anchors = _target_anchor_predicates("table_class")
    row = (
        op.get_bind()
        .execute(
            text(f"""
        SELECT table_class.oid AS oid, table_namespace.oid AS schema_oid,
               table_namespace.nspname AS schema, table_class.relname AS name
        FROM pg_catalog.pg_class AS table_class
        JOIN pg_catalog.pg_namespace AS table_namespace ON table_namespace.oid = table_class.relnamespace
        WHERE table_class.relname = 'upload_requests'
          AND table_class.relkind IN ('r', 'p')
          AND pg_catalog.left(table_namespace.nspname, 3)  OPERATOR(pg_catalog.<>)  'pg_'
          AND table_namespace.nspname  OPERATOR(pg_catalog.<>)  'information_schema'
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
               index_namespace.nspname AS schema, index_class.relname AS index_name,
               index_definition.indrelid AS table_oid,
               table_namespace.nspname AS table_schema, table_class.relname AS table_name,
               pg_catalog.array_agg(attribute.attname ORDER BY key_attribute.ordinality)
                   FILTER (WHERE key_attribute.ordinality <= index_definition.indnkeyatts)
                   AS key_columns,
               pg_catalog.array_agg(key_option.option ORDER BY key_attribute.ordinality)
                   FILTER (WHERE key_attribute.ordinality <= index_definition.indnkeyatts)
                   AS key_options,
               pg_catalog.array_agg(opclass.oid ORDER BY key_attribute.ordinality)
                   FILTER (WHERE key_attribute.ordinality <= index_definition.indnkeyatts)
                   AS key_opclasses,
               pg_catalog.array_agg(default_opclass.oid ORDER BY key_attribute.ordinality)
                   FILTER (WHERE key_attribute.ordinality <= index_definition.indnkeyatts)
                   AS expected_key_opclasses,
               pg_catalog.max(index_definition.indnkeyatts) AS key_column_count,
               pg_catalog.max(index_definition.indnatts) AS total_column_count, access_method.amname AS access_method,
               pg_catalog.bool_or(index_definition.indisunique) AS is_unique,
               pg_catalog.bool_or(index_definition.indpred IS NOT NULL) AS is_partial,
               pg_catalog.bool_or(index_definition.indexprs IS NOT NULL) AS is_expression,
               pg_catalog.bool_or(index_definition.indisexclusion) AS is_exclusion,
               pg_catalog.bool_or(index_definition.indisvalid) AS is_valid,
               pg_catalog.bool_or(index_definition.indisready) AS is_ready,
               pg_catalog.obj_description(index_class.oid, 'pg_class') AS ownership_comment
        FROM pg_catalog.pg_class AS index_class
        JOIN pg_catalog.pg_namespace AS index_namespace ON index_namespace.oid = index_class.relnamespace
        JOIN pg_catalog.pg_index AS index_definition ON index_definition.indexrelid = index_class.oid
        JOIN pg_catalog.pg_class AS table_class ON table_class.oid = index_definition.indrelid
        JOIN pg_catalog.pg_namespace AS table_namespace ON table_namespace.oid = table_class.relnamespace
        JOIN pg_catalog.pg_am AS access_method ON access_method.oid = index_class.relam
        LEFT JOIN LATERAL pg_catalog.unnest(index_definition.indkey)
            WITH ORDINALITY AS key_attribute(attnum, ordinality) ON TRUE
        LEFT JOIN LATERAL pg_catalog.unnest(index_definition.indoption)
            WITH ORDINALITY AS key_option(option, ordinality)
            ON key_option.ordinality = key_attribute.ordinality
        LEFT JOIN pg_catalog.pg_attribute AS attribute ON attribute.attrelid = index_definition.indrelid
            AND attribute.attnum = key_attribute.attnum
        LEFT JOIN LATERAL pg_catalog.unnest(index_definition.indclass)
            WITH ORDINALITY AS index_opclass(opclass_oid, ordinality)
            ON index_opclass.ordinality = key_attribute.ordinality
        LEFT JOIN pg_catalog.pg_opclass AS opclass ON opclass.oid = index_opclass.opclass_oid
        LEFT JOIN pg_catalog.pg_opclass AS default_opclass ON default_opclass.opcmethod = index_class.relam
            AND default_opclass.opcintype = attribute.atttypid AND default_opclass.opcdefault
        WHERE {name_predicate}{where}
        GROUP BY index_class.oid, index_namespace.oid, index_namespace.nspname,
                 index_class.relname, index_definition.indrelid,
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
            index_name=row["index_name"],
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


def _matches_expected_index(
    index: _IndexSignature, target: _TargetTable, *, expected_name: str = _INDEX_NAME
) -> bool:
    return (
        index.index_name == expected_name
        and index.schema == target.schema
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


def _matches_renamed_index(
    index: _IndexSignature,
    original: _IndexSignature,
    target: _TargetTable,
    temporary_name: str,
) -> bool:
    """Prove that the originally selected relation received the temporary name."""
    return (
        index.index_oid == original.index_oid
        and index.index_schema_oid == original.index_schema_oid == target.schema_oid
        and index.schema == original.schema == target.schema
        and index.index_name == temporary_name
        and _matches_expected_index(index, target, expected_name=temporary_name)
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


def _owned_index_oids_sql() -> str:
    """One row per marked index, including unrelated names, schemas and signatures."""
    return f"""
        SELECT index_class.oid
        FROM pg_catalog.pg_class AS index_class
        JOIN pg_catalog.pg_index AS index_definition
          ON index_definition.indexrelid OPERATOR(pg_catalog.=) index_class.oid
        WHERE pg_catalog.obj_description(index_class.oid, 'pg_class')
              OPERATOR(pg_catalog.=) '{_INDEX_OWNERSHIP_MARKER}'::pg_catalog.text
        ORDER BY index_class.oid
    """


def _owned_index_oids() -> list[int]:
    """Return every index bearing the exact marker, without target/signature filters."""
    return list(op.get_bind().execute(text(_owned_index_oids_sql())).scalars().all())


def _validate_upgrade_owners(index_oid: int | None, *, require_owned: bool = False) -> None:
    owners = _owned_index_oids()
    if len(owners) > 1:
        raise RuntimeError(f"Cannot apply {revision}: duplicate ownership markers.")
    if owners and owners != [index_oid]:
        raise RuntimeError(f"Cannot apply {revision}: ownership conflict with another index.")
    if require_owned and owners != [index_oid]:
        raise RuntimeError(f"Cannot apply {revision}: ownership marker was not stored.")


def _offline_upgrade_owners_sql(*, require_owned: bool = False) -> str:
    # MIN only transports the OID after the count proves there is one owner.
    required = (
        f"""IF owner_count OPERATOR(pg_catalog.<>) 1 THEN
    RAISE EXCEPTION 'Cannot apply {revision}: ownership marker was not stored';
  END IF;"""
        if require_owned
        else ""
    )
    return f"""
  SELECT pg_catalog.count(*), pg_catalog.min(owners.oid) INTO owner_count, owner_oid
    FROM ({_owned_index_oids_sql()}) AS owners;
  IF owner_count OPERATOR(pg_catalog.>) 1 THEN
    RAISE EXCEPTION 'Cannot apply {revision}: duplicate ownership markers';
  END IF;
  IF owner_count OPERATOR(pg_catalog.=) 1 AND
     (index_oid IS NULL OR owner_oid OPERATOR(pg_catalog.<>) index_oid) THEN
    RAISE EXCEPTION 'Cannot apply {revision}: ownership conflict with another index';
  END IF;
  {required}
"""


def _downgrade_candidates() -> list[_IndexSignature]:
    """Load all marker owners by OID; validation deliberately happens later."""
    candidates: list[_IndexSignature] = []
    for index_oid in _owned_index_oids():
        candidates.extend(
            _index_rows(
                "index_class.oid = :index_oid", {"index_oid": index_oid}, require_name=False
            )
        )
    return candidates


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
    _validate_upgrade_owners(index.index_oid)
    # Lock even an already marked index, keeping the selected identity stable until
    # the final global ownership check and Alembic's revision update commit together.
    temporary_name = f"__yd_0010_adopt_{index.index_oid}"
    qualified_name = f"{_quote_identifier(index.schema)}.{_quote_identifier(_INDEX_NAME)}"
    temporary = f"{_quote_identifier(index.schema)}.{_quote_identifier(temporary_name)}"
    marker = _INDEX_OWNERSHIP_MARKER.replace("'", "''")
    op.execute(text(f"ALTER INDEX {qualified_name} RENAME TO {_quote_identifier(temporary_name)}"))
    locked_rows = _index_rows(
        "index_class.oid = :index_oid", {"index_oid": index.index_oid}, require_name=False
    )
    if (
        len(locked_rows) != 1
        or not _matches_renamed_index(locked_rows[0], index, target, temporary_name)
        or locked_rows[0].ownership_comment != index.ownership_comment
    ):
        raise RuntimeError(f"Cannot apply {revision}: locked index validation failure.")
    _validate_upgrade_owners(index.index_oid)
    if index.ownership_comment is None:
        op.execute(text(f"COMMENT ON INDEX {temporary} IS '{marker}'"))
        marked_rows = _index_rows(
            "index_class.oid = :index_oid", {"index_oid": index.index_oid}, require_name=False
        )
        if len(marked_rows) != 1 or marked_rows[0].ownership_comment != _INDEX_OWNERSHIP_MARKER:
            raise RuntimeError(f"Cannot apply {revision}: ownership marker was not stored.")
    op.execute(text(f"ALTER INDEX {temporary} RENAME TO {_quote_identifier(_INDEX_NAME)}"))
    marked = _find_existing_index(target)
    _validate_existing_index(marked, target)
    if (
        marked is None
        or marked.index_oid != index.index_oid
        or marked.ownership_comment != _INDEX_OWNERSHIP_MARKER
    ):
        raise RuntimeError(
            f"Cannot apply {revision}: ownership marker was not stored for index {_INDEX_NAME}."
        )
    _validate_upgrade_owners(index.index_oid, require_owned=True)


def _offline_upgrade_sql() -> str:
    anchors = _target_anchor_predicates("c")
    return f"""
DO $$
DECLARE target_oid pg_catalog.oid; target_schema_oid pg_catalog.oid; target_schema pg_catalog.text;
        index_oid pg_catalog.oid; existing_comment pg_catalog.text; temporary_name pg_catalog.text;
        named_count pg_catalog.int8; valid_count pg_catalog.int8; target_count pg_catalog.int8;
        owner_count pg_catalog.int8; owner_oid pg_catalog.oid;
BEGIN
  {_offline_ownership_lock_sql()}
  SELECT pg_catalog.count(*),pg_catalog.min(c.oid),pg_catalog.min(n.oid),pg_catalog.min(n.nspname) INTO target_count,target_oid,target_schema_oid,target_schema FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE c.relname='upload_requests' AND c.relkind IN ('r','p') AND pg_catalog.left(n.nspname,3) OPERATOR(pg_catalog.<>) 'pg_' AND n.nspname OPERATOR(pg_catalog.<>) 'information_schema' AND {anchors};
  IF target_count=0 THEN RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: target table upload_requests was not found'; END IF;
  IF target_count>1 THEN RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: application target is ambiguous'; END IF;
  SELECT pg_catalog.count(*), pg_catalog.min(i.oid) INTO named_count, index_oid
    FROM pg_catalog.pg_class AS i
    WHERE i.relnamespace OPERATOR(pg_catalog.=) target_schema_oid
      AND i.relname OPERATOR(pg_catalog.=) '{_INDEX_NAME}'::pg_catalog.name;
  {_offline_upgrade_owners_sql()}
  IF named_count = 0 THEN EXECUTE pg_catalog.format('CREATE INDEX IF NOT EXISTS %I ON %I.%I (created_at, id)', 'ix_upload_requests_created_id', target_schema, 'upload_requests'); END IF;
  SELECT pg_catalog.count(*) INTO valid_count FROM pg_catalog.pg_class i JOIN pg_catalog.pg_namespace ins ON ins.oid = i.relnamespace JOIN pg_catalog.pg_index x ON x.indexrelid = i.oid JOIN pg_catalog.pg_class t ON t.oid = x.indrelid JOIN pg_catalog.pg_namespace tns ON tns.oid = t.relnamespace JOIN pg_catalog.pg_am am ON am.oid = i.relam WHERE i.relname = 'ix_upload_requests_created_id' AND ins.nspname = target_schema AND x.indrelid = target_oid AND ins.oid = tns.oid AND t.relname = 'upload_requests' AND x.indnkeyatts = 2 AND x.indnatts = 2 AND am.amname = 'btree' AND NOT x.indisunique AND NOT x.indisexclusion AND x.indpred IS NULL AND x.indexprs IS NULL AND x.indisvalid AND x.indisready AND (SELECT pg_catalog.array_agg(a.attname ORDER BY k.ordinality) FROM pg_catalog.unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_catalog.pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.attnum WHERE k.ordinality <= x.indnkeyatts) = ARRAY['created_at', 'id']::pg_catalog.name[] AND (SELECT pg_catalog.array_agg(o.option ORDER BY o.ordinality) FROM pg_catalog.unnest(x.indoption) WITH ORDINALITY AS o(option, ordinality) WHERE o.ordinality <= x.indnkeyatts) = ARRAY[0, 0]::pg_catalog.int2[] AND (SELECT pg_catalog.array_agg(ic.opclass_oid ORDER BY ic.ordinality) FROM pg_catalog.unnest(x.indclass) WITH ORDINALITY AS ic(opclass_oid, ordinality) WHERE ic.ordinality <= x.indnkeyatts) = (SELECT pg_catalog.array_agg(opc.oid ORDER BY k.ordinality) FROM pg_catalog.unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_catalog.pg_attribute a ON a.attrelid=x.indrelid AND a.attnum=k.attnum JOIN pg_catalog.pg_opclass opc ON opc.opcmethod=(SELECT oid FROM pg_catalog.pg_am WHERE amname='btree') AND opc.opcintype=a.atttypid AND opc.opcdefault WHERE k.ordinality <= x.indnkeyatts);
  IF valid_count = 0 THEN
    IF named_count = 0 THEN RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: index ix_upload_requests_created_id was not found after creation'; END IF;
    RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: index ix_upload_requests_created_id has an incompatible signature';
  END IF;
  SELECT i.oid, pg_catalog.obj_description(i.oid, 'pg_class') INTO STRICT index_oid, existing_comment
    FROM pg_catalog.pg_class i JOIN pg_catalog.pg_namespace n ON n.oid = i.relnamespace
    WHERE n.nspname = target_schema AND i.relname = 'ix_upload_requests_created_id';
  IF existing_comment IS NOT NULL AND existing_comment  OPERATOR(pg_catalog.<>)  '{_INDEX_OWNERSHIP_MARKER}' THEN
    RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: ownership conflict for index ix_upload_requests_created_id; its existing comment belongs to another owner';
  END IF;
  {_offline_upgrade_owners_sql()}
    temporary_name := '__yd_0010_adopt_'::pg_catalog.text OPERATOR(pg_catalog.||) index_oid::pg_catalog.text;
    EXECUTE pg_catalog.format('ALTER INDEX %I.%I RENAME TO %I', target_schema,
                   'ix_upload_requests_created_id', temporary_name);
    SELECT pg_catalog.count(*) INTO valid_count FROM pg_catalog.pg_class i JOIN pg_catalog.pg_namespace ins ON ins.oid=i.relnamespace JOIN pg_catalog.pg_index x ON x.indexrelid=i.oid JOIN pg_catalog.pg_class t ON t.oid=x.indrelid JOIN pg_catalog.pg_namespace tns ON tns.oid=t.relnamespace JOIN pg_catalog.pg_am am ON am.oid=i.relam WHERE i.oid=index_oid AND i.relname=temporary_name AND ins.oid=target_schema_oid AND ins.nspname=target_schema AND x.indrelid=target_oid AND ins.oid=tns.oid AND t.relname='upload_requests' AND ((pg_catalog.obj_description(i.oid,'pg_class') IS NULL AND existing_comment IS NULL) OR pg_catalog.obj_description(i.oid,'pg_class') OPERATOR(pg_catalog.=) existing_comment) AND x.indnkeyatts=2 AND x.indnatts=2 AND am.amname='btree' AND NOT x.indisunique AND NOT x.indisexclusion AND x.indpred IS NULL AND x.indexprs IS NULL AND x.indisvalid AND x.indisready AND (SELECT pg_catalog.array_agg(a.attname ORDER BY k.ordinality) FROM pg_catalog.unnest(x.indkey) WITH ORDINALITY k(attnum,ordinality) JOIN pg_catalog.pg_attribute a ON a.attrelid=x.indrelid AND a.attnum=k.attnum WHERE k.ordinality<=x.indnkeyatts)=ARRAY['created_at','id']::pg_catalog.name[] AND (SELECT pg_catalog.array_agg(o.option ORDER BY o.ordinality) FROM pg_catalog.unnest(x.indoption) WITH ORDINALITY o(option,ordinality) WHERE o.ordinality<=x.indnkeyatts)=ARRAY[0,0]::pg_catalog.int2[] AND (SELECT pg_catalog.array_agg(ic.opclass_oid ORDER BY ic.ordinality) FROM pg_catalog.unnest(x.indclass) WITH ORDINALITY ic(opclass_oid,ordinality) WHERE ic.ordinality<=x.indnkeyatts)=(SELECT pg_catalog.array_agg(opc.oid ORDER BY k.ordinality) FROM pg_catalog.unnest(x.indkey) WITH ORDINALITY k(attnum,ordinality) JOIN pg_catalog.pg_attribute a ON a.attrelid=x.indrelid AND a.attnum=k.attnum JOIN pg_catalog.pg_opclass opc ON opc.opcmethod=(SELECT oid FROM pg_catalog.pg_am WHERE amname='btree') AND opc.opcintype=a.atttypid AND opc.opcdefault WHERE k.ordinality<=x.indnkeyatts);
    IF valid_count  OPERATOR(pg_catalog.<>)  1 THEN RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: locked index validation failure'; END IF;
  {_offline_upgrade_owners_sql()}
  IF existing_comment IS NULL THEN
    EXECUTE pg_catalog.format('COMMENT ON INDEX %I.%I IS %L', target_schema,
                   temporary_name, '{_INDEX_OWNERSHIP_MARKER}');
  END IF;
    EXECUTE pg_catalog.format('ALTER INDEX %I.%I RENAME TO %I', target_schema,
                   temporary_name, 'ix_upload_requests_created_id');
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_class AS i
    JOIN pg_catalog.pg_index AS x ON x.indexrelid OPERATOR(pg_catalog.=) i.oid
    WHERE i.oid OPERATOR(pg_catalog.=) index_oid
      AND i.relname OPERATOR(pg_catalog.=) '{_INDEX_NAME}'::pg_catalog.name
      AND i.relnamespace OPERATOR(pg_catalog.=) target_schema_oid
      AND x.indrelid OPERATOR(pg_catalog.=) target_oid
      AND pg_catalog.obj_description(i.oid, 'pg_class')
          OPERATOR(pg_catalog.=) '{_INDEX_OWNERSHIP_MARKER}'::pg_catalog.text
  ) THEN
    RAISE EXCEPTION 'Cannot apply 0010_upload_created_index: ownership marker was not stored for index ix_upload_requests_created_id';
  END IF;
  {_offline_upgrade_owners_sql(require_owned=True)}
END $$;
"""


def _offline_downgrade_sql() -> str:
    anchors = _target_anchor_predicates("target_table")
    return f"""
DO $$
DECLARE target_count pg_catalog.int8; target_oid pg_catalog.oid; target_schema_oid pg_catalog.oid; target_schema pg_catalog.name; candidate_count pg_catalog.int8; candidate_schema pg_catalog.name; candidate_schema_oid pg_catalog.oid; candidate_schemas pg_catalog.text; candidate_oid pg_catalog.oid; candidate_table_oid pg_catalog.oid; locked_owner_oid pg_catalog.oid; temporary_name pg_catalog.text;
BEGIN
  {_offline_ownership_lock_sql()}
  SELECT pg_catalog.count(*), pg_catalog.min(target_table.oid), pg_catalog.min(target_namespace.oid), pg_catalog.min(target_namespace.nspname)
    INTO target_count, target_oid, target_schema_oid, target_schema
    FROM pg_catalog.pg_class AS target_table
    JOIN pg_catalog.pg_namespace AS target_namespace
      ON target_namespace.oid = target_table.relnamespace
    WHERE target_table.relname = 'upload_requests'
      AND target_table.relkind IN ('r', 'p')
      AND pg_catalog.left(target_namespace.nspname, 3) OPERATOR(pg_catalog.<>) 'pg_'
      AND target_namespace.nspname OPERATOR(pg_catalog.<>) 'information_schema'
      AND {anchors};
  IF target_count = 0 THEN RAISE EXCEPTION 'Cannot downgrade 0010_upload_created_index: application target was not found'; END IF;
  IF target_count > 1 THEN RAISE EXCEPTION 'Cannot downgrade 0010_upload_created_index: application target is ambiguous'; END IF;
  SELECT pg_catalog.count(*), pg_catalog.min(ins.nspname), pg_catalog.min(ins.oid),
         pg_catalog.string_agg(pg_catalog.format('%I', ins.nspname), ', ' ORDER BY i.oid),
         pg_catalog.min(i.oid), pg_catalog.min(x.indrelid)
    INTO candidate_count, candidate_schema, candidate_schema_oid, candidate_schemas,
         candidate_oid, candidate_table_oid
    FROM pg_catalog.pg_class AS i
    JOIN pg_catalog.pg_namespace AS ins ON ins.oid = i.relnamespace
    JOIN pg_catalog.pg_index AS x ON x.indexrelid = i.oid
    WHERE pg_catalog.obj_description(i.oid, 'pg_class') OPERATOR(pg_catalog.=)
          'yd_upd_approver:alembic:0010_upload_created_index';
  IF candidate_count = 0 THEN RAISE EXCEPTION 'Cannot downgrade 0010_upload_created_index: ownership marker was not found'; END IF;
  IF candidate_count > 1 THEN RAISE EXCEPTION 'Cannot downgrade 0010_upload_created_index: duplicate ownership markers in schemas: %', candidate_schemas; END IF;
  SELECT pg_catalog.count(*) INTO candidate_count FROM pg_catalog.pg_class i JOIN pg_catalog.pg_namespace ins ON ins.oid = i.relnamespace JOIN pg_catalog.pg_index x ON x.indexrelid = i.oid JOIN pg_catalog.pg_class t ON t.oid = x.indrelid JOIN pg_catalog.pg_namespace tns ON tns.oid = t.relnamespace JOIN pg_catalog.pg_am am ON am.oid = i.relam WHERE i.oid = candidate_oid AND i.relname = 'ix_upload_requests_created_id' AND ins.oid = target_schema_oid AND ins.nspname = target_schema AND x.indrelid = target_oid AND ins.oid = tns.oid AND t.relname = 'upload_requests' AND t.relkind IN ('r', 'p') AND pg_catalog.left(ins.nspname, 3) OPERATOR(pg_catalog.<>) 'pg_' AND ins.nspname OPERATOR(pg_catalog.<>) 'information_schema' AND pg_catalog.obj_description(i.oid, 'pg_class') = 'yd_upd_approver:alembic:0010_upload_created_index' AND x.indnkeyatts = 2 AND x.indnatts = 2 AND am.amname = 'btree' AND NOT x.indisunique AND NOT x.indisexclusion AND x.indpred IS NULL AND x.indexprs IS NULL AND x.indisvalid AND x.indisready AND (SELECT pg_catalog.array_agg(a.attname ORDER BY k.ordinality) FROM pg_catalog.unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_catalog.pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.attnum WHERE k.ordinality <= x.indnkeyatts) = ARRAY['created_at', 'id']::pg_catalog.name[] AND (SELECT pg_catalog.array_agg(o.option ORDER BY o.ordinality) FROM pg_catalog.unnest(x.indoption) WITH ORDINALITY AS o(option, ordinality) WHERE o.ordinality <= x.indnkeyatts) = ARRAY[0, 0]::pg_catalog.int2[] AND (SELECT pg_catalog.array_agg(ic.opclass_oid ORDER BY ic.ordinality) FROM pg_catalog.unnest(x.indclass) WITH ORDINALITY AS ic(opclass_oid, ordinality) WHERE ic.ordinality <= x.indnkeyatts) = (SELECT pg_catalog.array_agg(opc.oid ORDER BY k.ordinality) FROM pg_catalog.unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_catalog.pg_attribute a ON a.attrelid=x.indrelid AND a.attnum=k.attnum JOIN pg_catalog.pg_opclass opc ON opc.opcmethod=(SELECT oid FROM pg_catalog.pg_am WHERE amname='btree') AND opc.opcintype=a.atttypid AND opc.opcdefault WHERE k.ordinality <= x.indnkeyatts);
  IF candidate_count OPERATOR(pg_catalog.<>) 1 THEN
    RAISE EXCEPTION 'Cannot downgrade 0010_upload_created_index: incompatible managed index target';
  END IF;
  temporary_name := '__yd_0010_drop_' || candidate_oid::pg_catalog.text;
  EXECUTE pg_catalog.format('ALTER INDEX %I.%I RENAME TO %I', candidate_schema,
                            'ix_upload_requests_created_id', temporary_name);
  SELECT pg_catalog.count(*), pg_catalog.min(i.oid)
    INTO candidate_count, locked_owner_oid
    FROM pg_catalog.pg_class AS i
    JOIN pg_catalog.pg_index AS x ON x.indexrelid = i.oid
    WHERE pg_catalog.obj_description(i.oid, 'pg_class') OPERATOR(pg_catalog.=)
          'yd_upd_approver:alembic:0010_upload_created_index';
  IF candidate_count OPERATOR(pg_catalog.<>) 1
     OR locked_owner_oid OPERATOR(pg_catalog.<>) candidate_oid THEN
    RAISE EXCEPTION 'Cannot downgrade 0010_upload_created_index: locked index validation failure';
  END IF;
  SELECT pg_catalog.count(*) INTO candidate_count FROM pg_catalog.pg_class i JOIN pg_catalog.pg_namespace ins ON ins.oid = i.relnamespace JOIN pg_catalog.pg_index x ON x.indexrelid = i.oid JOIN pg_catalog.pg_class t ON t.oid = x.indrelid JOIN pg_catalog.pg_namespace tns ON tns.oid = t.relnamespace JOIN pg_catalog.pg_am am ON am.oid = i.relam WHERE i.oid = candidate_oid AND i.relname = temporary_name AND ins.oid = candidate_schema_oid AND ins.oid = target_schema_oid AND ins.nspname = target_schema AND x.indrelid = candidate_table_oid AND x.indrelid = target_oid AND ins.oid = tns.oid AND t.relname = 'upload_requests' AND t.relkind IN ('r', 'p') AND pg_catalog.left(ins.nspname, 3)  OPERATOR(pg_catalog.<>)  'pg_' AND ins.nspname  OPERATOR(pg_catalog.<>)  'information_schema' AND pg_catalog.obj_description(i.oid, 'pg_class') = 'yd_upd_approver:alembic:0010_upload_created_index' AND x.indnkeyatts = 2 AND x.indnatts = 2 AND am.amname = 'btree' AND NOT x.indisunique AND NOT x.indisexclusion AND x.indpred IS NULL AND x.indexprs IS NULL AND x.indisvalid AND x.indisready AND (SELECT pg_catalog.array_agg(a.attname ORDER BY k.ordinality) FROM pg_catalog.unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_catalog.pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.attnum WHERE k.ordinality <= x.indnkeyatts) = ARRAY['created_at', 'id']::pg_catalog.name[] AND (SELECT pg_catalog.array_agg(o.option ORDER BY o.ordinality) FROM pg_catalog.unnest(x.indoption) WITH ORDINALITY AS o(option, ordinality) WHERE o.ordinality <= x.indnkeyatts) = ARRAY[0, 0]::pg_catalog.int2[] AND (SELECT pg_catalog.array_agg(ic.opclass_oid ORDER BY ic.ordinality) FROM pg_catalog.unnest(x.indclass) WITH ORDINALITY AS ic(opclass_oid, ordinality) WHERE ic.ordinality <= x.indnkeyatts) = (SELECT pg_catalog.array_agg(opc.oid ORDER BY k.ordinality) FROM pg_catalog.unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) JOIN pg_catalog.pg_attribute a ON a.attrelid=x.indrelid AND a.attnum=k.attnum JOIN pg_catalog.pg_opclass opc ON opc.opcmethod=(SELECT oid FROM pg_catalog.pg_am WHERE amname='btree') AND opc.opcintype=a.atttypid AND opc.opcdefault WHERE k.ordinality <= x.indnkeyatts);
  IF candidate_count OPERATOR(pg_catalog.<>) 1 THEN
    RAISE EXCEPTION 'Cannot downgrade 0010_upload_created_index: locked index validation failure';
  END IF;
  IF pg_catalog.obj_description(candidate_oid, 'pg_class') IS DISTINCT FROM
       'yd_upd_approver:alembic:0010_upload_created_index' THEN
    RAISE EXCEPTION 'Cannot downgrade 0010_upload_created_index: locked index validation failure';
  END IF;
  EXECUTE pg_catalog.format('DROP INDEX %I.%I', candidate_schema, temporary_name);
END $$;
"""


def upgrade() -> None:
    if _is_offline_mode():
        op.execute(_offline_upgrade_sql())
        return
    _acquire_ownership_lock()
    target = _resolve_target_table()
    existing = _find_existing_index(target)
    if existing is not None:
        _validate_existing_index(existing, target)
    _validate_upgrade_owners(existing.index_oid if existing is not None else None)
    if existing is not None:
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
    _acquire_ownership_lock()
    target = _resolve_target_table()
    candidates = _downgrade_candidates()
    if not candidates:
        raise RuntimeError(f"Cannot downgrade {revision}: ownership marker was not found.")
    if len(candidates) > 1:
        schemas = ", ".join(repr(index.schema) for index in candidates)
        raise RuntimeError(
            f"Cannot downgrade {revision}: duplicate ownership markers in schemas: {schemas}."
        )
    candidate = candidates[0]
    if not _matches_expected_index(candidate, target):
        raise RuntimeError(f"Cannot downgrade {revision}: incompatible managed index target.")
    # Rename locks this exact relation until the migration transaction ends.  Re-read
    # by OID after lock acquisition so a concurrent DROP/recreate or COMMENT cannot
    # make us remove a different object merely reusing the managed name.
    temporary_name = f"__yd_0010_drop_{candidate.index_oid}"
    qualified = f"{_quote_identifier(candidate.schema)}.{_quote_identifier(_INDEX_NAME)}"
    temporary = f"{_quote_identifier(candidate.schema)}.{_quote_identifier(temporary_name)}"
    op.execute(text(f"ALTER INDEX {qualified} RENAME TO {_quote_identifier(temporary_name)}"))
    locked_rows = _index_rows(
        "index_class.oid = :index_oid", {"index_oid": candidate.index_oid}, require_name=False
    )
    locked_owner_oids = _owned_index_oids()
    if (
        locked_owner_oids != [candidate.index_oid]
        or len(locked_rows) != 1
        or not _matches_renamed_index(locked_rows[0], candidate, target, temporary_name)
        or locked_rows[0].ownership_comment != _INDEX_OWNERSHIP_MARKER
    ):
        raise RuntimeError(f"Cannot downgrade {revision}: locked index validation failure.")
    op.execute(text(f"DROP INDEX {temporary}"))
