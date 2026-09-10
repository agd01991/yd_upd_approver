"""database integrity and pagination indexes

Revision ID: 0009_db_integrity
Revises: 0008_telegram_outbox
Create Date: 2026-07-16
"""

import sqlalchemy as sa

from alembic import op

revision = "0009_db_integrity"
down_revision = "0008_telegram_outbox"
branch_labels = None
depends_on = None

_LEGACY_UPLOAD_INDEX_GUARD_SQL = r"""
DO $yd_0009_downgrade$
DECLARE
    target_count pg_catalog.int8;
    legacy_index_count pg_catalog.int8;
BEGIN
    WITH anchor_indexes AS (
        SELECT x.indrelid AS table_oid, i.relnamespace AS schema_oid, i.relname,
               pg_catalog.array_agg(a.attname::pg_catalog.text ORDER BY k.ordinality)
                   FILTER (WHERE k.ordinality OPERATOR(pg_catalog.<=)
                                 x.indnkeyatts::pg_catalog.int8) AS key_columns,
               pg_catalog.bool_or(x.indisunique) AS is_unique,
               pg_catalog.bool_or(x.indpred IS NOT NULL) AS is_partial,
               pg_catalog.bool_or(x.indexprs IS NOT NULL) AS is_expression,
               pg_catalog.bool_or(x.indisexclusion) AS is_exclusion,
               pg_catalog.bool_and(x.indisvalid) AS is_valid,
               pg_catalog.bool_and(x.indisready) AS is_ready,
               pg_catalog.max(x.indnkeyatts) AS key_count,
               pg_catalog.max(x.indnatts) AS total_count, am.amname
        FROM pg_catalog.pg_index AS x
        JOIN pg_catalog.pg_class AS i ON i.oid OPERATOR(pg_catalog.=) x.indexrelid
        JOIN pg_catalog.pg_am AS am ON am.oid OPERATOR(pg_catalog.=) i.relam
        JOIN LATERAL pg_catalog.unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) ON true
        JOIN pg_catalog.pg_attribute AS a
          ON a.attrelid OPERATOR(pg_catalog.=) x.indrelid
         AND a.attnum OPERATOR(pg_catalog.=) k.attnum
        WHERE i.relname OPERATOR(pg_catalog.=) ANY (
            ARRAY['ix_upload_requests_user_created_id',
                  'ix_upload_requests_status_created_id']::pg_catalog.name[])
        GROUP BY x.indrelid, i.relnamespace, i.relname, am.amname
    ), application_targets AS (
        SELECT t.oid, t.relnamespace
        FROM pg_catalog.pg_class AS t
        JOIN pg_catalog.pg_namespace AS n ON n.oid OPERATOR(pg_catalog.=) t.relnamespace
        WHERE t.relname OPERATOR(pg_catalog.=) 'upload_requests'::pg_catalog.name
          AND t.relkind OPERATOR(pg_catalog.=) ANY (ARRAY['r', 'p']::pg_catalog."char"[])
          AND pg_catalog.left(n.nspname::pg_catalog.text, 3) OPERATOR(pg_catalog.<>) 'pg_'
          AND n.nspname OPERATOR(pg_catalog.<>) 'information_schema'::pg_catalog.name
          AND EXISTS (
              SELECT 1 FROM anchor_indexes AS anchor
              WHERE anchor.table_oid OPERATOR(pg_catalog.=) t.oid
                AND anchor.schema_oid OPERATOR(pg_catalog.=) t.relnamespace
                AND anchor.relname OPERATOR(pg_catalog.=)
                    'ix_upload_requests_user_created_id'::pg_catalog.name
                AND anchor.key_columns OPERATOR(pg_catalog.=)
                    ARRAY['user_id', 'created_at', 'id']::pg_catalog.text[]
                AND anchor.key_count OPERATOR(pg_catalog.=) 3
                AND anchor.total_count OPERATOR(pg_catalog.=) 3
                AND anchor.amname OPERATOR(pg_catalog.=) 'btree'::pg_catalog.name
                AND NOT anchor.is_unique AND NOT anchor.is_partial
                AND NOT anchor.is_expression AND NOT anchor.is_exclusion
                AND anchor.is_valid AND anchor.is_ready)
          AND EXISTS (
              SELECT 1 FROM anchor_indexes AS anchor
              WHERE anchor.table_oid OPERATOR(pg_catalog.=) t.oid
                AND anchor.schema_oid OPERATOR(pg_catalog.=) t.relnamespace
                AND anchor.relname OPERATOR(pg_catalog.=)
                    'ix_upload_requests_status_created_id'::pg_catalog.name
                AND anchor.key_columns OPERATOR(pg_catalog.=)
                    ARRAY['status', 'created_at', 'id']::pg_catalog.text[]
                AND anchor.key_count OPERATOR(pg_catalog.=) 3
                AND anchor.total_count OPERATOR(pg_catalog.=) 3
                AND anchor.amname OPERATOR(pg_catalog.=) 'btree'::pg_catalog.name
                AND NOT anchor.is_unique AND NOT anchor.is_partial
                AND NOT anchor.is_expression AND NOT anchor.is_exclusion
                AND anchor.is_valid AND anchor.is_ready)
    )
    SELECT (SELECT pg_catalog.count(*) FROM application_targets),
           (SELECT pg_catalog.count(*) FROM application_targets AS target
            JOIN pg_catalog.pg_index AS x ON x.indrelid OPERATOR(pg_catalog.=) target.oid
            JOIN pg_catalog.pg_class AS i
              ON i.oid OPERATOR(pg_catalog.=) x.indexrelid
             AND i.relnamespace OPERATOR(pg_catalog.=) target.relnamespace
            WHERE i.relname OPERATOR(pg_catalog.=)
                  'ix_upload_requests_created_id'::pg_catalog.name)
      INTO target_count, legacy_index_count;

    IF target_count OPERATOR(pg_catalog.<>) 1 THEN
        RAISE EXCEPTION USING MESSAGE =
            'Cannot downgrade 0009_db_integrity: application upload_requests target is missing or ambiguous.';
    END IF;
    IF legacy_index_count OPERATOR(pg_catalog.<>) 0 THEN
        RAISE EXCEPTION USING MESSAGE =
            'Cannot downgrade 0009_db_integrity while ix_upload_requests_created_id exists on the application table. Upgrade through 0011_upload_index_ownership, verify the managed index and ownership marker, then downgrade to 0008_telegram_outbox.';
    END IF;
END
$yd_0009_downgrade$
"""


def _ensure_no_legacy_conflicts() -> None:
    bind = op.get_bind()
    duplicate_pending = bind.execute(
        sa.text("""
        SELECT user_id FROM folder_rename_requests
        WHERE status = 'pending'
        GROUP BY user_id HAVING count(*) > 1 LIMIT 1
    """)
    ).first()
    if duplicate_pending:
        raise RuntimeError(
            "Cannot add unique pending rename index: duplicate pending requests exist"
        )
    invalid_folders = bind.execute(
        sa.text("""
        SELECT id FROM users WHERE allowed_folders IS NULL OR jsonb_typeof(allowed_folders) <> 'array' LIMIT 1
    """)
    ).first()
    if invalid_folders:
        raise RuntimeError(
            "Cannot enforce users.allowed_folders JSON array: invalid legacy rows exist"
        )


def upgrade() -> None:
    _ensure_no_legacy_conflicts()
    op.execute("UPDATE users SET allowed_folders = '[]'::jsonb WHERE allowed_folders IS NULL")
    op.execute("UPDATE telegram_outbox SET payload = '{}'::jsonb WHERE payload IS NULL")
    op.execute("UPDATE upload_requests SET attempt_count = 0 WHERE attempt_count IS NULL")
    op.execute("UPDATE telegram_outbox SET attempt_count = 0 WHERE attempt_count IS NULL")
    op.alter_column(
        "users", "allowed_folders", server_default=sa.text("'[]'::jsonb"), nullable=False
    )
    op.alter_column(
        "telegram_outbox", "payload", server_default=sa.text("'{}'::jsonb"), nullable=False
    )
    op.alter_column("upload_requests", "attempt_count", server_default="0", nullable=False)
    op.alter_column("telegram_outbox", "attempt_count", server_default="0", nullable=False)

    op.create_check_constraint(
        "ck_users_quota_mb_non_negative", "users", "quota_mb IS NULL OR quota_mb >= 0"
    )
    op.create_check_constraint(
        "ck_users_allowed_folders_array", "users", "jsonb_typeof(allowed_folders) = 'array'"
    )
    op.create_check_constraint(
        "ck_upload_requests_size_bytes_non_negative", "upload_requests", "size_bytes >= 0"
    )
    op.create_check_constraint(
        "ck_upload_requests_attempt_count_non_negative", "upload_requests", "attempt_count >= 0"
    )
    op.create_check_constraint(
        "ck_telegram_outbox_attempt_count_non_negative", "telegram_outbox", "attempt_count >= 0"
    )
    op.create_check_constraint(
        "ck_telegram_outbox_payload_object", "telegram_outbox", "jsonb_typeof(payload) = 'object'"
    )

    op.create_index("ix_users_created_id", "users", ["created_at", "id"])
    op.create_index("ix_users_status_created_id", "users", ["status", "created_at", "id"])
    op.create_index(
        "ix_upload_requests_user_created_id", "upload_requests", ["user_id", "created_at", "id"]
    )
    op.create_index(
        "ix_upload_requests_status_created_id", "upload_requests", ["status", "created_at", "id"]
    )
    op.create_index("ix_audit_log_created_id", "audit_log", ["created_at", "id"])
    op.create_index(
        "ix_folder_rename_status_created_id",
        "folder_rename_requests",
        ["status", "created_at", "id"],
    )
    op.create_index(
        "uq_folder_rename_pending_user",
        "folder_rename_requests",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    for table, col, target, ondelete in [
        ("upload_requests", "user_id", "users", "RESTRICT"),
        ("folder_rename_requests", "user_id", "users", "RESTRICT"),
        ("audit_log", "request_id", "upload_requests", "SET NULL"),
        ("audit_log", "user_id", "users", "SET NULL"),
        ("telegram_outbox", "request_id", "upload_requests", "SET NULL"),
        ("telegram_outbox", "user_id", "users", "SET NULL"),
    ]:
        name = f"{table}_{col}_fkey"
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, target, [col], ["id"], ondelete=ondelete)


def downgrade() -> None:
    # An old version of this revision created an unmarked ordering index. Its name
    # and shape cannot prove ownership, so fail before changing any 0009 objects.
    op.execute(sa.text(_LEGACY_UPLOAD_INDEX_GUARD_SQL))
    for table, col, target in [
        ("telegram_outbox", "user_id", "users"),
        ("telegram_outbox", "request_id", "upload_requests"),
        ("audit_log", "user_id", "users"),
        ("audit_log", "request_id", "upload_requests"),
        ("folder_rename_requests", "user_id", "users"),
        ("upload_requests", "user_id", "users"),
    ]:
        name = f"{table}_{col}_fkey"
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, target, [col], ["id"])
    op.drop_index("uq_folder_rename_pending_user", table_name="folder_rename_requests")
    op.drop_index("ix_folder_rename_status_created_id", table_name="folder_rename_requests")
    op.drop_index("ix_audit_log_created_id", table_name="audit_log")
    op.drop_index("ix_upload_requests_status_created_id", table_name="upload_requests")
    op.drop_index("ix_upload_requests_user_created_id", table_name="upload_requests")
    op.drop_index("ix_users_status_created_id", table_name="users")
    op.drop_index("ix_users_created_id", table_name="users")
    op.drop_constraint("ck_telegram_outbox_payload_object", "telegram_outbox", type_="check")
    op.drop_constraint(
        "ck_telegram_outbox_attempt_count_non_negative", "telegram_outbox", type_="check"
    )
    op.drop_constraint(
        "ck_upload_requests_attempt_count_non_negative", "upload_requests", type_="check"
    )
    op.drop_constraint(
        "ck_upload_requests_size_bytes_non_negative", "upload_requests", type_="check"
    )
    op.drop_constraint("ck_users_allowed_folders_array", "users", type_="check")
    op.drop_constraint("ck_users_quota_mb_non_negative", "users", type_="check")
    op.alter_column("telegram_outbox", "payload", server_default=None)
    op.alter_column("users", "allowed_folders", server_default=None)
