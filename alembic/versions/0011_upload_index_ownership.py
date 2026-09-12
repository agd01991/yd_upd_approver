"""backfill ownership of the upload ordering index

Revision ID: 0011_upload_index_ownership
Revises: 0010_upload_created_index
"""

from dataclasses import dataclass

from sqlalchemy import text

from alembic import context, op

revision = "0011_upload_index_ownership"
down_revision = "0010_upload_created_index"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_upload_requests_created_id"
_INDEX_OWNERSHIP_MARKER = "yd_upd_approver:alembic:0010_upload_created_index"
_EXPECTED_KEY_COLUMNS = ("created_at", "id")
_EXPECTED_KEY_OPTIONS = (0, 0)
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


def _anchor_predicate(alias: str, columns: tuple[str, ...]) -> str:
    column_array = ",".join(f"'{column}'" for column in columns)
    return f"""{alias}.indnkeyatts = 3 AND {alias}.indnatts = 3 AND am.amname = 'btree'
      AND NOT {alias}.indisunique AND {alias}.indpred IS NULL AND {alias}.indexprs IS NULL
      AND NOT {alias}.indisexclusion AND {alias}.indisvalid AND {alias}.indisready
      AND (SELECT pg_catalog.array_agg(a.attname ORDER BY k.ordinality) FROM pg_catalog.unnest({alias}.indkey)
           WITH ORDINALITY k(attnum, ordinality) JOIN pg_catalog.pg_attribute a
           ON a.attrelid={alias}.indrelid AND a.attnum=k.attnum
           WHERE k.ordinality <= {alias}.indnkeyatts) = ARRAY[{column_array}]::pg_catalog.name[]
      AND (SELECT pg_catalog.array_agg(a.atttypid ORDER BY k.ordinality)
           FROM pg_catalog.unnest({alias}.indkey) WITH ORDINALITY k(attnum, ordinality)
           JOIN pg_catalog.pg_attribute a ON a.attrelid={alias}.indrelid AND a.attnum=k.attnum
           WHERE k.ordinality <= {alias}.indnkeyatts)
          = {_expected_anchor_type_oids(columns)}
      AND (SELECT pg_catalog.array_agg(o.option ORDER BY o.ordinality) FROM pg_catalog.unnest({alias}.indoption)
           WITH ORDINALITY o(option, ordinality) WHERE o.ordinality <= {alias}.indnkeyatts)
          = ARRAY[0,0,0]::pg_catalog.int2[]
      AND (SELECT pg_catalog.count(*) = {alias}.indnkeyatts
                  AND COALESCE(pg_catalog.bool_and((
                        ic.opclass_oid IS NOT NULL AND a.attnum IS NOT NULL
                        AND typ.oid IS NOT NULL AND opc.oid IS NOT NULL
                        AND opc.opcmethod = am.oid AND opc.opcdefault
                        AND (opc.opcintype = a.atttypid OR
                             (typ.typtype = 'e' AND
                              opc.opcintype = 'pg_catalog.anyenum'::pg_catalog.regtype))
                      ) IS TRUE), false)
           FROM pg_catalog.unnest({alias}.indkey) WITH ORDINALITY k(attnum, ordinality)
           LEFT JOIN pg_catalog.unnest({alias}.indclass) WITH ORDINALITY
             ic(opclass_oid, ordinality) ON ic.ordinality = k.ordinality
           LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid={alias}.indrelid AND a.attnum=k.attnum
           LEFT JOIN pg_catalog.pg_type typ ON typ.oid = a.atttypid
           LEFT JOIN pg_catalog.pg_opclass opc ON opc.oid = ic.opclass_oid
           WHERE k.ordinality <= {alias}.indnkeyatts)"""


def _target_anchor_predicates(table_alias: str) -> str:
    return " AND ".join(
        f"""EXISTS (SELECT 1 FROM pg_catalog.pg_class i JOIN pg_catalog.pg_index x ON x.indexrelid=i.oid
        JOIN pg_catalog.pg_am am ON am.oid=i.relam WHERE x.indrelid={table_alias}.oid
        AND i.relnamespace={table_alias}.relnamespace AND i.relname='{name}'
        AND {_anchor_predicate("x", columns)})"""
        for name, columns in _ANCHOR_SIGNATURES
    )


@dataclass(frozen=True)
class _IndexSignature:
    index_oid: int
    index_schema_oid: int
    schema: str
    table_oid: int
    table_schema: str
    table_name: str
    table_kind: str
    index_name: str
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


_INDEX_SELECT = """
SELECT i.oid AS index_oid, ins.oid AS index_schema_oid, ins.nspname AS schema,
       x.indrelid AS table_oid,
       tns.nspname AS table_schema, t.relname AS table_name, t.relkind::pg_catalog.text AS table_kind,
       i.relname AS index_name,
       pg_catalog.array_agg(a.attname ORDER BY k.ordinality)
         FILTER (WHERE k.ordinality <= x.indnkeyatts) AS key_columns,
       pg_catalog.array_agg(o.option ORDER BY k.ordinality)
         FILTER (WHERE k.ordinality <= x.indnkeyatts) AS key_options,
       pg_catalog.array_agg(opc.oid ORDER BY k.ordinality)
         FILTER (WHERE k.ordinality <= x.indnkeyatts) AS key_opclasses,
       pg_catalog.array_agg(default_opc.oid ORDER BY k.ordinality)
         FILTER (WHERE k.ordinality <= x.indnkeyatts) AS expected_key_opclasses,
       pg_catalog.max(x.indnkeyatts) AS key_column_count, pg_catalog.max(x.indnatts) AS total_column_count,
       am.amname AS access_method, pg_catalog.bool_or(x.indisunique) AS is_unique,
       pg_catalog.bool_or(x.indpred IS NOT NULL) AS is_partial,
       pg_catalog.bool_or(x.indexprs IS NOT NULL) AS is_expression,
       pg_catalog.bool_or(x.indisexclusion) AS is_exclusion,
       pg_catalog.bool_or(x.indisvalid) AS is_valid, pg_catalog.bool_or(x.indisready) AS is_ready,
       pg_catalog.obj_description(i.oid, 'pg_class') AS ownership_comment
FROM pg_catalog.pg_class i
JOIN pg_catalog.pg_namespace ins ON ins.oid = i.relnamespace
JOIN pg_catalog.pg_index x ON x.indexrelid = i.oid
JOIN pg_catalog.pg_class t ON t.oid = x.indrelid
JOIN pg_catalog.pg_namespace tns ON tns.oid = t.relnamespace
JOIN pg_catalog.pg_am am ON am.oid = i.relam
LEFT JOIN LATERAL pg_catalog.unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) ON TRUE
LEFT JOIN LATERAL pg_catalog.unnest(x.indoption) WITH ORDINALITY AS o(option, ordinality)
  ON o.ordinality = k.ordinality
LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.attnum
LEFT JOIN LATERAL pg_catalog.unnest(x.indclass) WITH ORDINALITY AS ic(opclass_oid, ordinality)
  ON ic.ordinality = k.ordinality
LEFT JOIN pg_catalog.pg_opclass opc ON opc.oid = ic.opclass_oid
LEFT JOIN pg_catalog.pg_opclass default_opc ON default_opc.opcmethod = i.relam
  AND default_opc.opcintype = a.atttypid AND default_opc.opcdefault
WHERE {where}
GROUP BY i.oid, ins.oid, ins.nspname, x.indrelid, tns.nspname, t.relname, t.relkind, i.relname,
         am.amname
"""


def _is_user_schema(schema: str) -> bool:
    return not schema.startswith("pg_") and schema != "information_schema"


def _rows(where: str, parameters: dict[str, object]) -> list[_IndexSignature]:
    result = op.get_bind().execute(text(_INDEX_SELECT.format(where=where)), parameters)
    return [
        _IndexSignature(
            **{
                **dict(row),
                "key_columns": tuple(row["key_columns"] or ()),
                "key_options": tuple(row["key_options"] or ()),
                "key_opclasses": tuple(row["key_opclasses"] or ()),
                "expected_key_opclasses": tuple(row["expected_key_opclasses"] or ()),
            }
        )
        for row in result.mappings().all()
    ]


def _owned_indexes() -> list[_IndexSignature]:
    # Deliberately do not filter by name/table: a misplaced expected marker is an error.
    return _rows(
        "pg_catalog.obj_description(i.oid, 'pg_class') = :marker",
        {"marker": _INDEX_OWNERSHIP_MARKER},
    )


def _target_index(target: _TargetTable) -> list[_IndexSignature]:
    return _rows(
        "i.relname = :index_name AND x.indrelid = :table_oid AND i.relnamespace = :schema_oid",
        {"index_name": _INDEX_NAME, "table_oid": target.oid, "schema_oid": target.schema_oid},
    )


def _matches(
    index: _IndexSignature, target: _TargetTable, *, expected_name: str = _INDEX_NAME
) -> bool:
    return (
        index.index_name == expected_name
        and index.index_schema_oid == target.schema_oid
        and index.table_oid == target.oid
        and index.schema == target.schema == index.table_schema
        and index.table_name == target.name
        and index.table_kind in ("r", "p")
        and _is_user_schema(index.schema)
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


def _resolve_application_target() -> _TargetTable:
    anchors = _target_anchor_predicates("t")
    rows = (
        op.get_bind()
        .execute(
            text(f"""
SELECT t.oid, n.oid AS schema_oid, n.nspname AS schema, t.relname AS name
FROM pg_catalog.pg_class t JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
WHERE t.relname = 'upload_requests' AND t.relkind IN ('r', 'p')
  AND pg_catalog.left(n.nspname, 3)  OPERATOR(pg_catalog.<>)  'pg_' AND n.nspname  OPERATOR(pg_catalog.<>)  'information_schema'
  AND {anchors}
""")
        )
        .mappings()
        .all()
    )
    if not rows:
        raise RuntimeError(f"Cannot apply {revision}: application target not found.")
    if len(rows) > 1:
        raise RuntimeError(f"Cannot apply {revision}: ambiguous application targets.")
    row = rows[0]
    return _TargetTable(row["oid"], row["schema_oid"], row["schema"], row["name"])


def _validate_owned(target: _TargetTable) -> _IndexSignature:
    owned = _owned_indexes()
    if not owned:
        raise RuntimeError(f"Cannot validate {revision}: managed index not found.")
    if len(owned) > 1:
        raise RuntimeError(f"Cannot validate {revision}: ambiguous owned indexes.")
    if not _matches(owned[0], target):
        raise RuntimeError(f"Cannot validate {revision}: incompatible index signature.")
    return owned[0]


def _quote(value: str) -> str:
    return op.get_bind().dialect.identifier_preparer.quote(value)


def _validate_oid(
    index_oid: int, target: _TargetTable, *, comment: str | None, expected_name: str = _INDEX_NAME
) -> _IndexSignature:
    rows = _rows("i.oid = :index_oid", {"index_oid": index_oid})
    if (
        len(rows) != 1
        or not _matches(rows[0], target, expected_name=expected_name)
        or rows[0].ownership_comment != comment
    ):
        raise RuntimeError(f"Cannot apply {revision}: locked index validation failure.")
    return rows[0]


def _validate_adoption_ownership(
    index_oid: int, target: _TargetTable, *, expected_name: str
) -> _IndexSignature:
    """Require the adoption candidate to be the sole global marker owner."""
    owned = _owned_indexes()
    if len(owned) != 1 or owned[0].index_oid != index_oid:
        raise RuntimeError(f"Cannot apply {revision}: ambiguous owned indexes.")
    if not _matches(owned[0], target, expected_name=expected_name):
        raise RuntimeError(f"Cannot apply {revision}: incompatible index signature.")
    return owned[0]


def _adopt(candidate: _IndexSignature, target: _TargetTable) -> None:
    """Serialize adoption using the index relation's transaction-held DDL lock."""
    temporary_name = f"__yd_0011_adopt_{candidate.index_oid}"
    qualified = f"{_quote(candidate.schema)}.{_quote(candidate.index_name)}"
    temporary = f"{_quote(candidate.schema)}.{_quote(temporary_name)}"
    # ALTER INDEX RENAME obtains SHARE UPDATE EXCLUSIVE on this exact relation.
    # It is self-conflicting, as is the lock taken by COMMENT ON INDEX.
    op.execute(text(f"ALTER INDEX {qualified} RENAME TO {_quote(temporary_name)}"))
    locked = _validate_oid(candidate.index_oid, target, comment=None, expected_name=temporary_name)
    if locked.index_name != temporary_name:
        raise RuntimeError(f"Cannot apply {revision}: locked index identity changed.")
    # The rename supplies the candidate-specific DDL lock.  Re-scan globally only
    # after acquiring it so a marker committed while the rename waited cannot be
    # missed by the initial scan in _online_upgrade().
    if _owned_indexes():
        raise RuntimeError(f"Cannot apply {revision}: ownership conflict.")
    marker = _INDEX_OWNERSHIP_MARKER.replace("'", "''")
    op.execute(text(f"COMMENT ON INDEX {temporary} IS '{marker}'"))
    marked = _validate_oid(
        candidate.index_oid,
        target,
        comment=_INDEX_OWNERSHIP_MARKER,
        expected_name=temporary_name,
    )
    if marked.index_name != temporary_name:
        raise RuntimeError(f"Cannot apply {revision}: marker identity changed.")
    _validate_adoption_ownership(candidate.index_oid, target, expected_name=temporary_name)
    op.execute(text(f"ALTER INDEX {temporary} RENAME TO {_quote(_INDEX_NAME)}"))
    final = _validate_oid(candidate.index_oid, target, comment=_INDEX_OWNERSHIP_MARKER)
    if final.index_name != _INDEX_NAME:
        raise RuntimeError(f"Cannot apply {revision}: marker post-validation failure.")
    _validate_adoption_ownership(candidate.index_oid, target, expected_name=_INDEX_NAME)


def _online_upgrade() -> None:
    target = _resolve_application_target()
    owned = _owned_indexes()
    if owned:
        _validate_owned(target)
        return
    candidates = _target_index(target)
    if not candidates:
        raise RuntimeError(f"Cannot apply {revision}: managed index not found.")
    if len(candidates) > 1 or not _matches(candidates[0], target):
        raise RuntimeError(f"Cannot apply {revision}: incompatible index signature.")
    candidate = candidates[0]
    if candidate.ownership_comment is not None:
        raise RuntimeError(f"Cannot apply {revision}: ownership conflict.")
    _adopt(candidate, target)


def _signature_predicate(alias: str = "x") -> str:
    return f"""{alias}.indnkeyatts = 2 AND {alias}.indnatts = 2 AND am.amname = 'btree'
 AND NOT {alias}.indisunique AND {alias}.indpred IS NULL AND {alias}.indexprs IS NULL
 AND NOT {alias}.indisexclusion
 AND {alias}.indisvalid AND {alias}.indisready
 AND (SELECT pg_catalog.array_agg(a.attname ORDER BY k.ordinality) FROM pg_catalog.unnest({alias}.indkey)
      WITH ORDINALITY k(attnum, ordinality) JOIN pg_catalog.pg_attribute a
      ON a.attrelid={alias}.indrelid AND a.attnum=k.attnum
      WHERE k.ordinality <= {alias}.indnkeyatts) = ARRAY['created_at','id']::pg_catalog.name[]
 AND (SELECT pg_catalog.array_agg(o.option ORDER BY o.ordinality) FROM pg_catalog.unnest({alias}.indoption)
      WITH ORDINALITY o(option, ordinality) WHERE o.ordinality <= {alias}.indnkeyatts)
      = ARRAY[0,0]::pg_catalog.int2[]
 AND (SELECT pg_catalog.array_agg(ic.opclass_oid ORDER BY ic.ordinality)
      FROM pg_catalog.unnest({alias}.indclass) WITH ORDINALITY ic(opclass_oid, ordinality)
      WHERE ic.ordinality <= {alias}.indnkeyatts)
     = (SELECT pg_catalog.array_agg(opc.oid ORDER BY k.ordinality)
        FROM pg_catalog.unnest({alias}.indkey) WITH ORDINALITY k(attnum, ordinality)
        JOIN pg_catalog.pg_attribute a ON a.attrelid={alias}.indrelid AND a.attnum=k.attnum
        JOIN pg_catalog.pg_opclass opc ON opc.opcmethod=(SELECT oid FROM pg_catalog.pg_am WHERE amname='btree')
          AND opc.opcintype=a.atttypid AND opc.opcdefault
        WHERE k.ordinality <= {alias}.indnkeyatts)"""


def _offline_sql(*, backfill: bool) -> str:
    anchors = _target_anchor_predicates("t")
    action = "apply" if backfill else "downgrade"
    backfill_sql = (
        ""
        if not backfill
        else f"""
  IF owned_count = 0 THEN
    SELECT pg_catalog.count(*),pg_catalog.min(i.oid),pg_catalog.min(pg_catalog.obj_description(i.oid,'pg_class')) INTO candidate_count,index_oid,existing_comment FROM pg_catalog.pg_class i JOIN pg_catalog.pg_index x ON x.indexrelid=i.oid JOIN pg_catalog.pg_class t ON t.oid=x.indrelid JOIN pg_catalog.pg_namespace n ON n.oid=i.relnamespace JOIN pg_catalog.pg_am am ON am.oid=i.relam WHERE i.relname='{_INDEX_NAME}' AND x.indrelid=target_oid AND i.relnamespace=target_schema_oid AND t.relname='upload_requests' AND t.relkind IN ('r','p') AND {_signature_predicate()};
    IF candidate_count=0 THEN RAISE EXCEPTION 'Cannot apply {revision}: managed index not found or incompatible index signature'; END IF;
    IF candidate_count>1 THEN RAISE EXCEPTION 'Cannot apply {revision}: ambiguous managed indexes'; END IF;
    IF existing_comment IS NOT NULL THEN RAISE EXCEPTION 'Cannot apply {revision}: ownership conflict'; END IF;
    temporary_name := '__yd_0011_adopt_' || index_oid::pg_catalog.text;
    EXECUTE pg_catalog.format('ALTER INDEX %I.%I RENAME TO %I',target_schema,'{_INDEX_NAME}',temporary_name);
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class i JOIN pg_catalog.pg_index x ON x.indexrelid=i.oid JOIN pg_catalog.pg_class t ON t.oid=x.indrelid JOIN pg_catalog.pg_namespace n ON n.oid=i.relnamespace JOIN pg_catalog.pg_am am ON am.oid=i.relam WHERE i.oid=index_oid AND i.relname=temporary_name AND x.indrelid=target_oid AND i.relnamespace=target_schema_oid AND pg_catalog.obj_description(i.oid,'pg_class') IS NULL AND {_signature_predicate()}) THEN RAISE EXCEPTION 'Cannot apply {revision}: locked index validation failure'; END IF;
    SELECT pg_catalog.count(*) INTO owned_count FROM pg_catalog.pg_class i JOIN pg_catalog.pg_index x ON x.indexrelid=i.oid WHERE pg_catalog.obj_description(i.oid,'pg_class')='{_INDEX_OWNERSHIP_MARKER}';
    IF owned_count OPERATOR(pg_catalog.<>) 0 THEN RAISE EXCEPTION 'Cannot apply {revision}: ownership conflict'; END IF;
    EXECUTE pg_catalog.format('COMMENT ON INDEX %I.%I IS %L',target_schema,temporary_name,'{_INDEX_OWNERSHIP_MARKER}');
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class i JOIN pg_catalog.pg_index x ON x.indexrelid=i.oid JOIN pg_catalog.pg_class t ON t.oid=x.indrelid JOIN pg_catalog.pg_namespace n ON n.oid=i.relnamespace JOIN pg_catalog.pg_am am ON am.oid=i.relam WHERE i.oid=index_oid AND i.relname=temporary_name AND x.indrelid=target_oid AND i.relnamespace=target_schema_oid AND pg_catalog.obj_description(i.oid,'pg_class')='{_INDEX_OWNERSHIP_MARKER}' AND {_signature_predicate()}) THEN RAISE EXCEPTION 'Cannot apply {revision}: marker post-validation failure'; END IF;
    SELECT pg_catalog.count(*),pg_catalog.min(i.oid) INTO owned_count,owned_oid FROM pg_catalog.pg_class i JOIN pg_catalog.pg_index x ON x.indexrelid=i.oid WHERE pg_catalog.obj_description(i.oid,'pg_class')='{_INDEX_OWNERSHIP_MARKER}';
    IF owned_count OPERATOR(pg_catalog.<>) 1 OR owned_oid OPERATOR(pg_catalog.<>) index_oid THEN RAISE EXCEPTION 'Cannot apply {revision}: ambiguous owned indexes'; END IF;
    EXECUTE pg_catalog.format('ALTER INDEX %I.%I RENAME TO %I',target_schema,temporary_name,'{_INDEX_NAME}');
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class i JOIN pg_catalog.pg_index x ON x.indexrelid=i.oid JOIN pg_catalog.pg_class t ON t.oid=x.indrelid JOIN pg_catalog.pg_namespace n ON n.oid=i.relnamespace JOIN pg_catalog.pg_am am ON am.oid=i.relam WHERE i.oid=index_oid AND i.relname='{_INDEX_NAME}' AND x.indrelid=target_oid AND i.relnamespace=target_schema_oid AND pg_catalog.obj_description(i.oid,'pg_class')='{_INDEX_OWNERSHIP_MARKER}' AND {_signature_predicate()}) THEN RAISE EXCEPTION 'Cannot apply {revision}: marker post-validation failure'; END IF;
    SELECT pg_catalog.count(*),pg_catalog.min(i.oid) INTO owned_count,owned_oid FROM pg_catalog.pg_class i JOIN pg_catalog.pg_index x ON x.indexrelid=i.oid WHERE pg_catalog.obj_description(i.oid,'pg_class')='{_INDEX_OWNERSHIP_MARKER}';
    IF owned_count OPERATOR(pg_catalog.<>) 1 OR owned_oid OPERATOR(pg_catalog.<>) index_oid THEN RAISE EXCEPTION 'Cannot apply {revision}: ambiguous owned indexes'; END IF;
  END IF;"""
    )
    return f"""
DO $$
DECLARE owned_count integer; compatible_owned_count integer; target_count integer;
 target_oid oid; target_schema_oid oid; target_schema text; candidate_count integer;
 index_oid oid; owned_oid oid; existing_comment text; temporary_name text;
BEGIN
 SELECT pg_catalog.count(*),pg_catalog.min(t.oid),pg_catalog.min(n.oid),pg_catalog.min(n.nspname) INTO target_count,target_oid,target_schema_oid,target_schema
 FROM pg_catalog.pg_class t JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
 WHERE t.relname='upload_requests' AND t.relkind IN ('r','p') AND pg_catalog.left(n.nspname,3) OPERATOR(pg_catalog.<>) 'pg_' AND n.nspname OPERATOR(pg_catalog.<>) 'information_schema'
 AND {anchors};
 IF target_count=0 THEN RAISE EXCEPTION 'Cannot {action} {revision}: application target not found'; END IF;
 IF target_count>1 THEN RAISE EXCEPTION 'Cannot {action} {revision}: ambiguous application targets'; END IF;
 SELECT pg_catalog.count(*),pg_catalog.count(*) FILTER (WHERE i.relname='{_INDEX_NAME}' AND i.relnamespace=target_schema_oid AND x.indrelid=target_oid AND n.nspname=tn.nspname AND t.relname='upload_requests' AND t.relkind IN ('r','p') AND pg_catalog.left(n.nspname,3) OPERATOR(pg_catalog.<>) 'pg_' AND n.nspname OPERATOR(pg_catalog.<>) 'information_schema' AND {_signature_predicate()})
 INTO owned_count,compatible_owned_count FROM pg_catalog.pg_class i JOIN pg_catalog.pg_namespace n ON n.oid=i.relnamespace JOIN pg_catalog.pg_index x ON x.indexrelid=i.oid JOIN pg_catalog.pg_class t ON t.oid=x.indrelid JOIN pg_catalog.pg_namespace tn ON tn.oid=t.relnamespace JOIN pg_catalog.pg_am am ON am.oid=i.relam WHERE pg_catalog.obj_description(i.oid,'pg_class')='{_INDEX_OWNERSHIP_MARKER}';
 IF owned_count>1 THEN RAISE EXCEPTION 'Cannot {action} {revision}: ambiguous owned indexes'; END IF;
 IF owned_count=1 AND compatible_owned_count OPERATOR(pg_catalog.<>) 1 THEN RAISE EXCEPTION 'Cannot {action} {revision}: incompatible index signature'; END IF;
 {backfill_sql}
 IF owned_count=0 AND {str(not backfill).upper()} THEN RAISE EXCEPTION 'Cannot downgrade {revision}: managed index not found'; END IF;
END $$;
"""


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute(_offline_sql(backfill=True))
    else:
        _online_upgrade()


def downgrade() -> None:
    if context.is_offline_mode():
        op.execute(_offline_sql(backfill=False))
    else:
        _validate_owned(_resolve_application_target())
