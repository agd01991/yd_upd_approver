"""make pagination timestamps total and add rename history index

Revision ID: 0012_pagination_created_at
Revises: 0011_upload_index_ownership

Historical NULL timestamps have no recoverable event time.  They receive a
stable sentinel ordered by primary key: 1970-01-01 UTC plus ``id``
microseconds.  Existing non-NULL timestamps are never changed.
"""

import sqlalchemy as sa

from alembic import op

revision = "0012_pagination_created_at"
down_revision = "0011_upload_index_ownership"
branch_labels = None
depends_on = None

_TABLES = ("users", "upload_requests", "audit_log", "folder_rename_requests")
_RENAME_INDEX = "ix_folder_rename_user_created_id"


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            sa.text(
                f"UPDATE {table} SET created_at = "
                "TIMESTAMPTZ '1970-01-01 00:00:00+00' + id * INTERVAL '1 microsecond' "
                "WHERE created_at IS NULL"
            )
        )
        op.alter_column(
            table,
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
    op.create_index(
        _RENAME_INDEX,
        "folder_rename_requests",
        ["user_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(_RENAME_INDEX, table_name="folder_rename_requests")
    for table in reversed(_TABLES):
        op.alter_column(
            table,
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
