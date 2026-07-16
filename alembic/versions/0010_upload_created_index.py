"""add global upload request ordering index

Revision ID: 0010_upload_created_index
Revises: 0009_db_integrity
"""

from dataclasses import dataclass

from sqlalchemy import text

from alembic import op

revision = "0010_upload_created_index"
down_revision = "0009_db_integrity"
branch_labels = None
depends_on = None


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


def _resolve_target_table() -> _TargetTable:
    """Resolve upload_requests exactly as PostgreSQL resolves an unqualified relation."""
    row = (
        op.get_bind()
        .execute(
            text(
                """
                SELECT
                    table_class.oid AS oid,
                    table_namespace.oid AS schema_oid,
                    table_namespace.nspname AS schema,
                    table_class.relname AS name
                FROM pg_class AS table_class
                JOIN pg_namespace AS table_namespace
                    ON table_namespace.oid = table_class.relnamespace
                WHERE table_class.oid = to_regclass('upload_requests')
                    AND table_class.relkind IN ('r', 'p')
                """
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise RuntimeError(
            "Cannot apply 0010_upload_created_index: upload_requests does not resolve "
            "to an ordinary or partitioned table."
        )
    return _TargetTable(
        oid=row["oid"], schema_oid=row["schema_oid"], schema=row["schema"], name=row["name"]
    )


def _find_existing_index(target: _TargetTable) -> _IndexSignature | None:
    """Return the named index in the resolved table's schema without parsing index SQL."""
    row = (
        op.get_bind()
        .execute(
            text(
                """
                SELECT
                    index_namespace.nspname AS schema,
                    index_definition.indrelid AS table_oid,
                    table_namespace.nspname AS table_schema,
                    table_class.relname AS table_name,
                    array_agg(attribute.attname ORDER BY key_attribute.ordinality)
                        FILTER (WHERE key_attribute.ordinality <= index_definition.indnkeyatts)
                        AS key_columns,
                    max(index_definition.indnkeyatts) AS key_column_count,
                    max(index_definition.indnatts) AS total_column_count,
                    access_method.amname AS access_method,
                    bool_or(index_definition.indisunique) AS is_unique,
                    bool_or(index_definition.indpred IS NOT NULL) AS is_partial,
                    bool_or(index_definition.indexprs IS NOT NULL) AS is_expression,
                    bool_or(index_definition.indisvalid) AS is_valid,
                    bool_or(index_definition.indisready) AS is_ready
                FROM pg_class AS index_class
                JOIN pg_namespace AS index_namespace
                    ON index_namespace.oid = index_class.relnamespace
                JOIN pg_index AS index_definition
                    ON index_definition.indexrelid = index_class.oid
                JOIN pg_class AS table_class
                    ON table_class.oid = index_definition.indrelid
                JOIN pg_namespace AS table_namespace
                    ON table_namespace.oid = table_class.relnamespace
                JOIN pg_am AS access_method
                    ON access_method.oid = index_class.relam
                LEFT JOIN LATERAL unnest(index_definition.indkey)
                    WITH ORDINALITY AS key_attribute(attnum, ordinality)
                    ON TRUE
                LEFT JOIN pg_attribute AS attribute
                    ON attribute.attrelid = index_definition.indrelid
                    AND attribute.attnum = key_attribute.attnum
                WHERE index_namespace.oid = :schema_oid
                    AND index_class.relname = 'ix_upload_requests_created_id'
                GROUP BY
                    index_namespace.nspname,
                    index_definition.indrelid,
                    table_namespace.nspname,
                    table_class.relname,
                    access_method.amname
                """
            ),
            {"schema_oid": target.schema_oid},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None

    return _IndexSignature(
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


def upgrade() -> None:
    # Support databases that applied the intermediate PR #76 version of 0009, which created it.
    target = _resolve_target_table()
    existing = _find_existing_index(target)
    if existing is None:
        op.create_index(
            "ix_upload_requests_created_id",
            target.name,
            ["created_at", "id"],
            schema=target.schema,
        )
        return

    if _matches_expected_index(existing, target):
        return

    raise RuntimeError(
        "Cannot apply 0010_upload_created_index: index "
        "ix_upload_requests_created_id exists with an unexpected definition. "
        "Expected table upload_requests with key columns (created_at, id); "
        f"found table {existing.table_schema}.{existing.table_name} with key columns "
        f"{list(existing.key_columns)!r}. Check the conflicting index and rename it or "
        "otherwise resolve it after taking a backup before retrying the migration."
    )


def downgrade() -> None:
    target = _resolve_target_table()
    op.drop_index(
        "ix_upload_requests_created_id",
        table_name=target.name,
        schema=target.schema,
    )
