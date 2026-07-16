"""add global upload request ordering index

Revision ID: 0010_upload_created_index
Revises: 0009_db_integrity
"""

from alembic import op

revision = "0010_upload_created_index"
down_revision = "0009_db_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Support databases that applied the intermediate PR #76 version of 0009, which created it.
    op.create_index(
        "ix_upload_requests_created_id",
        "upload_requests",
        ["created_at", "id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_upload_requests_created_id",
        table_name="upload_requests",
    )
