"""repair queued legacy retry upload modes

Revision ID: 0007_repair_queued_legacy_retries
Revises: 0006_repair_upload_mode_backfill
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_repair_queued_legacy_retries"
down_revision: str | None = "0006_repair_upload_mode_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


QUEUED_LEGACY_RETRY_REPAIR_SQL = """
WITH candidates AS (
    SELECT ur.id
    FROM upload_requests AS ur
    WHERE ur.status = 'approved'
      AND ur.queued_at IS NOT NULL
      AND ur.attempt_count = 0
      AND ur.last_attempt_at IS NULL
      AND ur.worker_token IS NULL
      AND ur.lease_expires_at IS NULL
      AND EXISTS (
          SELECT 1
          FROM audit_log AS retry
          WHERE retry.request_id = ur.id
            AND retry.action = 'upload_retry'
            AND NOT EXISTS (
                SELECT 1
                FROM audit_log AS newer
                WHERE newer.request_id = ur.id
                  AND newer.id > retry.id
                  AND newer.action IN (
                      'upload_approve',
                      'upload_retry',
                      'upload_overwrite',
                      'upload_copy',
                      'upload_copy_path'
                  )
            )
      )
    FOR UPDATE
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
                FROM audit_log AS copy_action
                WHERE copy_action.request_id = ur.id
                  AND copy_action.action IN ('upload_copy', 'upload_copy_path')
                  AND NOT EXISTS (
                    SELECT 1
                    FROM audit_log AS newer_choice
                    WHERE newer_choice.request_id = ur.id
                      AND newer_choice.id > copy_action.id
                      AND newer_choice.action IN (
                        'upload_overwrite',
                        'upload_copy',
                        'upload_copy_path'
                      )
                  )
             )
                THEN 'copy'::uploadmode
            ELSE 'normal'::uploadmode
        END AS repaired_upload_mode
    FROM upload_requests AS ur
    JOIN candidates ON candidates.id = ur.id
)
UPDATE upload_requests AS ur
SET upload_mode = classified.repaired_upload_mode
FROM classified
WHERE classified.id = ur.id
  AND ur.status = 'approved'
  AND ur.queued_at IS NOT NULL
  AND ur.attempt_count = 0
  AND ur.last_attempt_at IS NULL
  AND ur.worker_token IS NULL
  AND ur.lease_expires_at IS NULL
  AND EXISTS (
      SELECT 1
      FROM audit_log AS retry
      WHERE retry.request_id = ur.id
        AND retry.action = 'upload_retry'
        AND NOT EXISTS (
            SELECT 1
            FROM audit_log AS newer
            WHERE newer.request_id = ur.id
              AND newer.id > retry.id
              AND newer.action IN (
                  'upload_approve',
                  'upload_retry',
                  'upload_overwrite',
                  'upload_copy',
                  'upload_copy_path'
              )
        )
  )
  AND ur.upload_mode IS DISTINCT FROM classified.repaired_upload_mode
"""


def upgrade() -> None:
    # Repair queued legacy retries before the durable worker can claim them. The candidate CTE
    # locks eligible rows while their audit history is classified, and the UPDATE repeats the queue
    # eligibility predicate so already-claimed rows are not modified if their state changed.
    op.execute(QUEUED_LEGACY_RETRY_REPAIR_SQL)


def downgrade() -> None:
    # Data correction is intentionally irreversible: previous values may already be known-bad and
    # were not stored separately.
    pass
