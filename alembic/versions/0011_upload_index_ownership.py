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


@dataclass(frozen=True)
class _IndexSignature:
    index_oid: int
    schema: str
    table_oid: int
    table_schema: str
    table_name: str
    table_kind: str
    index_name: str
    key_columns: tuple[str | None, ...]
    key_options: tuple[int, ...]
    key_column_count: int
    total_column_count: int
    access_method: str
    is_unique: bool
    is_partial: bool
    is_expression: bool
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
SELECT i.oid AS index_oid, ins.nspname AS schema, x.indrelid AS table_oid,
       tns.nspname AS table_schema, t.relname AS table_name, t.relkind::text AS table_kind,
       i.relname AS index_name,
       array_agg(a.attname ORDER BY k.ordinality)
         FILTER (WHERE k.ordinality <= x.indnkeyatts) AS key_columns,
       array_agg(o.option ORDER BY k.ordinality)
         FILTER (WHERE k.ordinality <= x.indnkeyatts) AS key_options,
       max(x.indnkeyatts) AS key_column_count, max(x.indnatts) AS total_column_count,
       am.amname AS access_method, bool_or(x.indisunique) AS is_unique,
       bool_or(x.indpred IS NOT NULL) AS is_partial,
       bool_or(x.indexprs IS NOT NULL) AS is_expression,
       bool_or(x.indisvalid) AS is_valid, bool_or(x.indisready) AS is_ready,
       obj_description(i.oid, 'pg_class') AS ownership_comment
FROM pg_class i
JOIN pg_namespace ins ON ins.oid = i.relnamespace
JOIN pg_index x ON x.indexrelid = i.oid
JOIN pg_class t ON t.oid = x.indrelid
JOIN pg_namespace tns ON tns.oid = t.relnamespace
JOIN pg_am am ON am.oid = i.relam
LEFT JOIN LATERAL unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) ON TRUE
LEFT JOIN LATERAL unnest(x.indoption) WITH ORDINALITY AS o(option, ordinality)
  ON o.ordinality = k.ordinality
LEFT JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.attnum
WHERE {where}
GROUP BY i.oid, ins.nspname, x.indrelid, tns.nspname, t.relname, t.relkind, i.relname,
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
            }
        )
        for row in result.mappings().all()
    ]


def _owned_indexes() -> list[_IndexSignature]:
    # Deliberately do not filter by name/table: a misplaced expected marker is an error.
    return _rows(
        "obj_description(i.oid, 'pg_class') = :marker", {"marker": _INDEX_OWNERSHIP_MARKER}
    )


def _target_index(target: _TargetTable) -> list[_IndexSignature]:
    return _rows(
        "i.relname = :index_name AND x.indrelid = :table_oid AND i.relnamespace = :schema_oid",
        {"index_name": _INDEX_NAME, "table_oid": target.oid, "schema_oid": target.schema_oid},
    )


def _matches(index: _IndexSignature) -> bool:
    return (
        index.index_name == _INDEX_NAME
        and index.schema == index.table_schema
        and index.table_name == "upload_requests"
        and index.table_kind in ("r", "p")
        and _is_user_schema(index.schema)
        and index.key_columns == _EXPECTED_KEY_COLUMNS
        and index.key_options == _EXPECTED_KEY_OPTIONS
        and index.key_column_count == 2
        and index.total_column_count == 2
        and index.access_method == "btree"
        and not index.is_unique
        and not index.is_partial
        and not index.is_expression
        and index.is_valid
        and index.is_ready
    )


def _resolve_application_target() -> _TargetTable:
    rows = (
        op.get_bind()
        .execute(
            text("""
SELECT t.oid, n.oid AS schema_oid, n.nspname AS schema, t.relname AS name
FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE t.relname = 'upload_requests' AND t.relkind IN ('r', 'p')
  AND left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema'
  AND EXISTS (
    SELECT 1 FROM pg_class i JOIN pg_index x ON x.indexrelid = i.oid
    JOIN pg_am am ON am.oid = i.relam
    WHERE x.indrelid = t.oid AND i.relnamespace = t.relnamespace
      AND i.relname = 'ix_upload_requests_user_created_id'
      AND x.indnkeyatts = 3 AND x.indnatts = 3 AND am.amname = 'btree'
      AND NOT x.indisunique AND x.indpred IS NULL AND x.indexprs IS NULL
      AND x.indisvalid AND x.indisready
      AND (SELECT array_agg(a.attname ORDER BY k.ordinality)
           FROM unnest(x.indkey) WITH ORDINALITY k(attnum, ordinality)
           JOIN pg_attribute a ON a.attrelid=x.indrelid AND a.attnum=k.attnum
           WHERE k.ordinality <= x.indnkeyatts) = ARRAY['user_id','created_at','id']::name[]
      AND (SELECT array_agg(o.option ORDER BY o.ordinality)
           FROM unnest(x.indoption) WITH ORDINALITY o(option, ordinality)
           WHERE o.ordinality <= x.indnkeyatts) = ARRAY[0,0,0]::smallint[])
  AND EXISTS (
    SELECT 1 FROM pg_class i JOIN pg_index x ON x.indexrelid = i.oid
    JOIN pg_am am ON am.oid = i.relam
    WHERE x.indrelid = t.oid AND i.relnamespace = t.relnamespace
      AND i.relname = 'ix_upload_requests_status_created_id'
      AND x.indnkeyatts = 3 AND x.indnatts = 3 AND am.amname = 'btree'
      AND NOT x.indisunique AND x.indpred IS NULL AND x.indexprs IS NULL
      AND x.indisvalid AND x.indisready
      AND (SELECT array_agg(a.attname ORDER BY k.ordinality)
           FROM unnest(x.indkey) WITH ORDINALITY k(attnum, ordinality)
           JOIN pg_attribute a ON a.attrelid=x.indrelid AND a.attnum=k.attnum
           WHERE k.ordinality <= x.indnkeyatts) = ARRAY['status','created_at','id']::name[]
      AND (SELECT array_agg(o.option ORDER BY o.ordinality)
           FROM unnest(x.indoption) WITH ORDINALITY o(option, ordinality)
           WHERE o.ordinality <= x.indnkeyatts) = ARRAY[0,0,0]::smallint[])
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


def _validate_owned() -> _IndexSignature:
    owned = _owned_indexes()
    if not owned:
        raise RuntimeError(f"Cannot validate {revision}: managed index not found.")
    if len(owned) > 1:
        raise RuntimeError(f"Cannot validate {revision}: ambiguous owned indexes.")
    if not _matches(owned[0]):
        raise RuntimeError(f"Cannot validate {revision}: incompatible index signature.")
    return owned[0]


def _quote(value: str) -> str:
    return op.get_bind().dialect.identifier_preparer.quote(value)


def _online_upgrade() -> None:
    owned = _owned_indexes()
    if owned:
        _validate_owned()
        return
    target = _resolve_application_target()
    candidates = _target_index(target)
    if not candidates:
        raise RuntimeError(f"Cannot apply {revision}: managed index not found.")
    if len(candidates) > 1 or not _matches(candidates[0]):
        raise RuntimeError(f"Cannot apply {revision}: incompatible index signature.")
    candidate = candidates[0]
    if candidate.ownership_comment is not None:
        raise RuntimeError(f"Cannot apply {revision}: ownership conflict.")
    marker = _INDEX_OWNERSHIP_MARKER.replace("'", "''")
    op.execute(
        text(
            f"COMMENT ON INDEX {_quote(candidate.schema)}.{_quote(candidate.index_name)} IS '{marker}'"
        )
    )
    post = _rows("i.oid = :index_oid", {"index_oid": candidate.index_oid})
    if (
        len(post) != 1
        or not _matches(post[0])
        or post[0].ownership_comment != _INDEX_OWNERSHIP_MARKER
    ):
        raise RuntimeError(f"Cannot apply {revision}: marker post-validation failure.")


def _signature_predicate(alias: str = "x") -> str:
    return f"""{alias}.indnkeyatts = 2 AND {alias}.indnatts = 2 AND am.amname = 'btree'
 AND NOT {alias}.indisunique AND {alias}.indpred IS NULL AND {alias}.indexprs IS NULL
 AND {alias}.indisvalid AND {alias}.indisready
 AND (SELECT array_agg(a.attname ORDER BY k.ordinality) FROM unnest({alias}.indkey)
      WITH ORDINALITY k(attnum, ordinality) JOIN pg_attribute a
      ON a.attrelid={alias}.indrelid AND a.attnum=k.attnum
      WHERE k.ordinality <= {alias}.indnkeyatts) = ARRAY['created_at','id']::name[]
 AND (SELECT array_agg(o.option ORDER BY o.ordinality) FROM unnest({alias}.indoption)
      WITH ORDINALITY o(option, ordinality) WHERE o.ordinality <= {alias}.indnkeyatts)
      = ARRAY[0,0]::smallint[]"""


def _offline_sql(*, backfill: bool) -> str:
    action = "apply" if backfill else "downgrade"
    backfill_sql = (
        ""
        if not backfill
        else f"""
  IF owned_count = 0 THEN
    SELECT count(*), min(t.oid), min(n.oid), min(n.nspname) INTO target_count,target_oid,target_schema_oid,target_schema
    FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
    WHERE t.relname='upload_requests' AND t.relkind IN ('r','p')
      AND left(n.nspname,3) <> 'pg_' AND n.nspname <> 'information_schema'
      AND EXISTS (SELECT 1 FROM pg_class i JOIN pg_index x ON x.indexrelid=i.oid JOIN pg_am am ON am.oid=i.relam WHERE x.indrelid=t.oid AND i.relnamespace=t.relnamespace AND i.relname='ix_upload_requests_user_created_id' AND x.indnkeyatts=3 AND x.indnatts=3 AND am.amname='btree' AND NOT x.indisunique AND x.indpred IS NULL AND x.indexprs IS NULL AND x.indisvalid AND x.indisready AND (SELECT array_agg(a.attname ORDER BY k.ordinality) FROM unnest(x.indkey) WITH ORDINALITY k(attnum,ordinality) JOIN pg_attribute a ON a.attrelid=x.indrelid AND a.attnum=k.attnum WHERE k.ordinality<=x.indnkeyatts)=ARRAY['user_id','created_at','id']::name[] AND (SELECT array_agg(o.option ORDER BY o.ordinality) FROM unnest(x.indoption) WITH ORDINALITY o(option,ordinality) WHERE o.ordinality<=x.indnkeyatts)=ARRAY[0,0,0]::smallint[])
      AND EXISTS (SELECT 1 FROM pg_class i JOIN pg_index x ON x.indexrelid=i.oid JOIN pg_am am ON am.oid=i.relam WHERE x.indrelid=t.oid AND i.relnamespace=t.relnamespace AND i.relname='ix_upload_requests_status_created_id' AND x.indnkeyatts=3 AND x.indnatts=3 AND am.amname='btree' AND NOT x.indisunique AND x.indpred IS NULL AND x.indexprs IS NULL AND x.indisvalid AND x.indisready AND (SELECT array_agg(a.attname ORDER BY k.ordinality) FROM unnest(x.indkey) WITH ORDINALITY k(attnum,ordinality) JOIN pg_attribute a ON a.attrelid=x.indrelid AND a.attnum=k.attnum WHERE k.ordinality<=x.indnkeyatts)=ARRAY['status','created_at','id']::name[] AND (SELECT array_agg(o.option ORDER BY o.ordinality) FROM unnest(x.indoption) WITH ORDINALITY o(option,ordinality) WHERE o.ordinality<=x.indnkeyatts)=ARRAY[0,0,0]::smallint[]);
    IF target_count=0 THEN RAISE EXCEPTION 'Cannot apply {revision}: application target not found'; END IF;
    IF target_count>1 THEN RAISE EXCEPTION 'Cannot apply {revision}: ambiguous application targets'; END IF;
    SELECT count(*),min(i.oid),min(obj_description(i.oid,'pg_class')) INTO candidate_count,index_oid,existing_comment FROM pg_class i JOIN pg_index x ON x.indexrelid=i.oid JOIN pg_class t ON t.oid=x.indrelid JOIN pg_namespace n ON n.oid=i.relnamespace JOIN pg_am am ON am.oid=i.relam WHERE i.relname='{_INDEX_NAME}' AND x.indrelid=target_oid AND i.relnamespace=target_schema_oid AND t.relname='upload_requests' AND t.relkind IN ('r','p') AND {_signature_predicate()};
    IF candidate_count=0 THEN RAISE EXCEPTION 'Cannot apply {revision}: managed index not found or incompatible index signature'; END IF;
    IF candidate_count>1 THEN RAISE EXCEPTION 'Cannot apply {revision}: ambiguous managed indexes'; END IF;
    IF existing_comment IS NOT NULL THEN RAISE EXCEPTION 'Cannot apply {revision}: ownership conflict'; END IF;
    EXECUTE format('COMMENT ON INDEX %I.%I IS %L',target_schema,'{_INDEX_NAME}','{_INDEX_OWNERSHIP_MARKER}');
    IF NOT EXISTS (SELECT 1 FROM pg_class i JOIN pg_index x ON x.indexrelid=i.oid JOIN pg_class t ON t.oid=x.indrelid JOIN pg_namespace n ON n.oid=i.relnamespace JOIN pg_am am ON am.oid=i.relam WHERE i.oid=index_oid AND i.relname='{_INDEX_NAME}' AND x.indrelid=target_oid AND n.nspname=target_schema AND t.relkind IN ('r','p') AND obj_description(i.oid,'pg_class')='{_INDEX_OWNERSHIP_MARKER}' AND {_signature_predicate()}) THEN RAISE EXCEPTION 'Cannot apply {revision}: marker post-validation failure'; END IF;
  END IF;"""
    )
    return f"""
DO $$
DECLARE owned_count integer; compatible_owned_count integer; target_count integer;
 target_oid oid; target_schema_oid oid; target_schema text; candidate_count integer;
 index_oid oid; existing_comment text;
BEGIN
 SELECT count(*),count(*) FILTER (WHERE i.relname='{_INDEX_NAME}' AND n.nspname=tn.nspname AND t.relname='upload_requests' AND t.relkind IN ('r','p') AND left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND {_signature_predicate()})
 INTO owned_count,compatible_owned_count FROM pg_class i JOIN pg_namespace n ON n.oid=i.relnamespace JOIN pg_index x ON x.indexrelid=i.oid JOIN pg_class t ON t.oid=x.indrelid JOIN pg_namespace tn ON tn.oid=t.relnamespace JOIN pg_am am ON am.oid=i.relam WHERE obj_description(i.oid,'pg_class')='{_INDEX_OWNERSHIP_MARKER}';
 IF owned_count>1 THEN RAISE EXCEPTION 'Cannot {action} {revision}: ambiguous owned indexes'; END IF;
 IF owned_count=1 AND compatible_owned_count<>1 THEN RAISE EXCEPTION 'Cannot {action} {revision}: incompatible index signature'; END IF;
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
        _validate_owned()
