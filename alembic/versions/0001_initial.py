"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-07
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    user_status = postgresql.ENUM(
        "pending", "active", "rejected", "blocked", name="userstatus", create_type=False
    )
    upload_status = postgresql.ENUM(
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
        name="uploadstatus",
        create_type=False,
    )
    user_status.create(op.get_bind(), checkfirst=True)
    upload_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(255)),
        sa.Column("full_name", sa.String(512)),
        sa.Column("status", user_status, nullable=False),
        sa.Column("root_folder", sa.String(1024)),
        sa.Column("allowed_folders", postgresql.JSONB(), nullable=False),
        sa.Column("quota_mb", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by", sa.BigInteger()),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)
    op.create_index("ix_users_status", "users", ["status"])
    op.create_table(
        "upload_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_code", sa.String(32), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("telegram_file_id", sa.String(512), nullable=False),
        sa.Column("telegram_file_unique_id", sa.String(512)),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("safe_filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(255)),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("caption", sa.Text()),
        sa.Column("local_path", sa.String(2048), nullable=False),
        sa.Column("target_folder", sa.String(1024), nullable=False),
        sa.Column("target_path", sa.String(1536), nullable=False),
        sa.Column("status", upload_status, nullable=False),
        sa.Column("admin_comment", sa.Text()),
        sa.Column("reject_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by", sa.BigInteger()),
        sa.Column("uploaded_at", sa.DateTime(timezone=True)),
        sa.Column("rejected_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
    )
    op.create_index(
        "ix_upload_requests_request_code", "upload_requests", ["request_code"], unique=True
    )
    op.create_index("ix_upload_requests_user_id", "upload_requests", ["user_id"])
    op.create_index("ix_upload_requests_status", "upload_requests", ["status"])
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("upload_requests.id")),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("old_value", postgresql.JSONB()),
        sa.Column("new_value", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("upload_requests")
    op.drop_table("users")
    sa.Enum(name="uploadstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="userstatus").drop(op.get_bind(), checkfirst=True)
