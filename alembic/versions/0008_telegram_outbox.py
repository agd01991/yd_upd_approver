"""telegram outbox

Revision ID: 0008_telegram_outbox
Revises: 0007_repair_queued_retries
Create Date: 2026-07-13
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008_telegram_outbox"
down_revision = "0007_repair_queued_retries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    outbox_status = postgresql.ENUM(
        "pending",
        "processing",
        "sent",
        "discarded",
        "dead",
        name="telegramoutboxstatus",
        create_type=False,
    )
    outbox_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "upload_requests", sa.Column("source_event_key", sa.String(length=256), nullable=True)
    )
    op.create_index(
        "ix_upload_requests_source_event_key", "upload_requests", ["source_event_key"], unique=True
    )
    op.create_table(
        "telegram_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("recipient_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dedup_key", sa.String(length=512), nullable=False),
        sa.Column("status", outbox_status, nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("lock_token", sa.String(length=64), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_message_id", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("upload_requests.id"), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("dedup_key", name="uq_telegram_outbox_dedup_key"),
    )
    op.create_index(
        "ix_telegram_outbox_claim", "telegram_outbox", ["status", "next_attempt_at", "id"]
    )
    op.create_index("ix_telegram_outbox_lease", "telegram_outbox", ["status", "locked_until", "id"])
    op.create_index("ix_telegram_outbox_request_id", "telegram_outbox", ["request_id"])
    op.create_index("ix_telegram_outbox_user_id", "telegram_outbox", ["user_id"])
    op.create_index(
        "ix_telegram_outbox_recipient_telegram_id", "telegram_outbox", ["recipient_telegram_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_outbox_recipient_telegram_id", table_name="telegram_outbox")
    op.drop_index("ix_telegram_outbox_user_id", table_name="telegram_outbox")
    op.drop_index("ix_telegram_outbox_request_id", table_name="telegram_outbox")
    op.drop_index("ix_telegram_outbox_lease", table_name="telegram_outbox")
    op.drop_index("ix_telegram_outbox_claim", table_name="telegram_outbox")
    op.drop_table("telegram_outbox")
    op.drop_index("ix_upload_requests_source_event_key", table_name="upload_requests")
    op.drop_column("upload_requests", "source_event_key")
    postgresql.ENUM(name="telegramoutboxstatus", create_type=False).drop(
        op.get_bind(), checkfirst=True
    )
