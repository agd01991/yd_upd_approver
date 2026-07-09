"""add user folder names and rename requests

Revision ID: 0004_user_folder_names
Revises: 0003_app_settings
Create Date: 2026-07-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_user_folder_names"
down_revision: str | None = "0003_app_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status = postgresql.ENUM(
        "pending",
        "approved",
        "rejected",
        "cancelled",
        name="folderrenamerequeststatus",
        create_type=False,
    )
    status.create(op.get_bind(), checkfirst=True)
    op.add_column("users", sa.Column("folder_name", sa.String(length=512), nullable=True))
    op.add_column("users", sa.Column("contract_number", sa.String(length=128), nullable=True))
    op.add_column("users", sa.Column("contract_date", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("contract_full_name", sa.String(length=512), nullable=True))
    op.create_table(
        "folder_rename_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("requested_folder_name", sa.String(length=512), nullable=False),
        sa.Column("contract_number", sa.String(length=128), nullable=True),
        sa.Column("contract_date", sa.String(length=64), nullable=True),
        sa.Column("contract_full_name", sa.String(length=512), nullable=True),
        sa.Column("status", status, nullable=False),
        sa.Column("source_folder", sa.String(length=1024), nullable=True),
        sa.Column("target_folder", sa.String(length=1024), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_folder_rename_requests_user_id", "folder_rename_requests", ["user_id"])
    op.create_index("ix_folder_rename_requests_status", "folder_rename_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_folder_rename_requests_status", table_name="folder_rename_requests")
    op.drop_index("ix_folder_rename_requests_user_id", table_name="folder_rename_requests")
    op.drop_table("folder_rename_requests")
    for column in ["contract_full_name", "contract_date", "contract_number", "folder_name"]:
        op.drop_column("users", column)
    postgresql.ENUM(name="folderrenamerequeststatus").drop(op.get_bind(), checkfirst=True)
