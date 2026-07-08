"""app settings

Revision ID: 0003_app_settings
Revises: 0002_mini_app_upload_source
Create Date: 2026-07-08
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_app_settings"
down_revision = "0002_mini_app_upload_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
