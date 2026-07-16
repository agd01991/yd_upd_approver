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
