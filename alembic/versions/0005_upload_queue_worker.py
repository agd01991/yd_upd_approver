"""add durable upload worker queue fields

Revision ID: 0005_upload_queue_worker
Revises: 0004_user_folder_names
Create Date: 2026-07-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_upload_queue_worker"
down_revision: str | None = "0004_user_folder_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    upload_mode = postgresql.ENUM(
        "normal", "copy", "overwrite", name="uploadmode", create_type=False
    )
    upload_mode.create(bind, checkfirst=True)
    op.add_column("upload_requests", sa.Column("upload_mode", upload_mode, nullable=True))
    op.add_column(
        "upload_requests", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("upload_requests", sa.Column("attempt_count", sa.Integer(), nullable=True))
    op.add_column("upload_requests", sa.Column("worker_token", sa.String(length=64), nullable=True))
    op.add_column(
        "upload_requests", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "upload_requests", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE upload_requests SET upload_mode = 'normal' WHERE upload_mode IS NULL")
    op.execute("UPDATE upload_requests SET attempt_count = 0 WHERE attempt_count IS NULL")
    op.execute(
        """
        UPDATE upload_requests
        SET queued_at = COALESCE(approved_at, created_at)
        WHERE status = 'approved' AND queued_at IS NULL
        """
    )
    op.alter_column("upload_requests", "upload_mode", nullable=False)
    op.alter_column("upload_requests", "attempt_count", nullable=False)
    op.create_index("ix_upload_requests_upload_mode", "upload_requests", ["upload_mode"])
    op.create_index("ix_upload_requests_queued_at", "upload_requests", ["queued_at"])
    op.create_index("ix_upload_requests_worker_token", "upload_requests", ["worker_token"])
    op.create_index("ix_upload_requests_lease_expires_at", "upload_requests", ["lease_expires_at"])
    op.create_index(
        "ix_upload_requests_queue_order",
        "upload_requests",
        ["status", "queued_at", "id"],
    )
    op.create_index(
        "ix_upload_requests_stale_lease",
        "upload_requests",
        ["status", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_upload_requests_stale_lease", table_name="upload_requests")
    op.drop_index("ix_upload_requests_queue_order", table_name="upload_requests")
    op.drop_index("ix_upload_requests_lease_expires_at", table_name="upload_requests")
    op.drop_index("ix_upload_requests_worker_token", table_name="upload_requests")
    op.drop_index("ix_upload_requests_queued_at", table_name="upload_requests")
    op.drop_index("ix_upload_requests_upload_mode", table_name="upload_requests")
    for column in [
        "last_attempt_at",
        "lease_expires_at",
        "worker_token",
        "attempt_count",
        "queued_at",
        "upload_mode",
    ]:
        op.drop_column("upload_requests", column)
    postgresql.ENUM(name="uploadmode").drop(op.get_bind(), checkfirst=True)
