"""mini app upload source

Revision ID: 0002_mini_app_upload_source
Revises: 0001_initial
Create Date: 2026-07-08
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_mini_app_upload_source"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = postgresql.ENUM("telegram", "mini_app", name="uploadsource", create_type=False)
    source.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "upload_requests", sa.Column("source", source, nullable=False, server_default="telegram")
    )
    op.create_index("ix_upload_requests_source", "upload_requests", ["source"])
    op.alter_column(
        "upload_requests", "telegram_file_id", existing_type=sa.String(512), nullable=True
    )
    op.alter_column("upload_requests", "source", server_default=None)


def downgrade() -> None:
    op.alter_column(
        "upload_requests", "telegram_file_id", existing_type=sa.String(512), nullable=False
    )
    op.drop_index("ix_upload_requests_source", table_name="upload_requests")
    op.drop_column("upload_requests", "source")
    postgresql.ENUM(name="uploadsource").drop(op.get_bind(), checkfirst=True)
