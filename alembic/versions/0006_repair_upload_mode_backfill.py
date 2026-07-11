"""repair legacy upload mode backfill

Revision ID: 0006_repair_upload_mode_backfill
Revises: 0005_upload_queue_worker
Create Date: 2026-07-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_repair_upload_mode_backfill"
down_revision: str | None = "0005_upload_queue_worker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_UPLOAD_MODE_REPAIR_SQL = """
WITH legacy_failed AS (
    SELECT ur.id
    FROM upload_requests AS ur
    WHERE ur.status = 'failed'
      AND ur.attempt_count = 0
      AND ur.queued_at IS NULL
      AND ur.last_attempt_at IS NULL
      AND ur.worker_token IS NULL
      AND ur.lease_expires_at IS NULL
), classified AS (
    SELECT
        ur.id,
        CASE
            WHEN ur.target_path IS NOT NULL
             AND ur.target_folder IS NOT NULL
             AND ur.safe_filename IS NOT NULL
             AND ur.target_path <> ur.target_folder || ur.safe_filename
             AND EXISTS (
                SELECT 1
                FROM audit_log AS al
                WHERE al.request_id = ur.id
                  AND al.action IN ('upload_copy', 'upload_copy_path')
                  AND NOT EXISTS (
                    SELECT 1
                    FROM audit_log AS newer
                    WHERE newer.request_id = ur.id
                      AND newer.action IN (
                        'upload_overwrite',
                        'upload_copy',
                        'upload_copy_path'
                      )
                      AND newer.id > al.id
                  )
             )
                THEN 'copy'::uploadmode
            WHEN ur.target_path IS NOT NULL
             AND ur.target_folder IS NOT NULL
             AND ur.safe_filename IS NOT NULL
             AND ur.target_path = ur.target_folder || ur.safe_filename
             AND EXISTS (
                SELECT 1
                FROM audit_log AS al
                WHERE al.request_id = ur.id
                  AND al.action = 'upload_overwrite'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM audit_log AS newer
                    WHERE newer.request_id = ur.id
                      AND newer.action IN (
                        'upload_approve',
                        'upload_retry',
                        'upload_overwrite',
                        'upload_copy',
                        'upload_copy_path'
                      )
                      AND newer.id > al.id
                  )
             )
                THEN 'overwrite'::uploadmode
            ELSE 'normal'::uploadmode
        END AS repaired_upload_mode
    FROM upload_requests AS ur
    JOIN legacy_failed ON legacy_failed.id = ur.id
)
UPDATE upload_requests AS ur
SET upload_mode = classified.repaired_upload_mode
FROM classified
WHERE classified.id = ur.id
  AND ur.upload_mode IS DISTINCT FROM classified.repaired_upload_mode
"""


def upgrade() -> None:
    # Only legacy failed rows that were never queued or leased by the durable worker are repaired.
    # New worker actions set queued_at for enqueue and attempt/lease fields when processing; those
    # rows already treat upload_mode as explicit audit history and must not be reclassified here.
    # The CASE recomputes every matching row from source audit_log facts, so re-running it is
    # idempotent and also fixes stale non-NULL values produced by an earlier 0005 execution.
    op.execute(LEGACY_UPLOAD_MODE_REPAIR_SQL)


def downgrade() -> None:
    # Data correction is intentionally irreversible: previous values may already be known-bad and
    # were not stored separately.
    pass
