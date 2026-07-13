from pathlib import Path

COPY_SUPERSEDING_ACTIONS = {
    "upload_overwrite",
    "upload_copy",
    "upload_copy_path",
}
OVERWRITE_SUPERSEDING_ACTIONS = {
    "upload_approve",
    "upload_retry",
    "upload_overwrite",
    "upload_copy",
    "upload_copy_path",
}
COPY_MARKER_ACTIONS = {"upload_copy", "upload_copy_path"}
OVERWRITE_MARKER_ACTION = "upload_overwrite"


def _legacy_mode(
    *, target_path="/dst/file.txt", target_folder="/dst/", safe_filename="file.txt", audit_events=()
):
    """Mirror the 0005 backfill contract for legacy failed uploads.

    Legacy retry reused target_path but called upload with overwrite=False. Therefore it preserves
    copy-path behavior but supersedes overwrite behavior.
    """
    canonical_path = target_folder + safe_filename

    has_current_copy_marker = False
    has_current_overwrite_marker = False
    for event in sorted(audit_events, key=lambda item: item["id"]):
        if event["request_id"] != 1:
            continue
        action = event["action"]
        if action in COPY_MARKER_ACTIONS:
            has_current_copy_marker = True
        elif action in COPY_SUPERSEDING_ACTIONS:
            has_current_copy_marker = False

        if action == OVERWRITE_MARKER_ACTION:
            has_current_overwrite_marker = True
        elif action in OVERWRITE_SUPERSEDING_ACTIONS:
            has_current_overwrite_marker = False

    if target_path != canonical_path and has_current_copy_marker:
        return "copy"
    if target_path == canonical_path and has_current_overwrite_marker:
        return "overwrite"
    return "normal"


def _migration_source() -> str:
    return Path("alembic/versions/0005_upload_queue_worker.py").read_text()


def _constant_sql(migration: str, name: str) -> str:
    marker = f'{name} = """'
    start = migration.index(marker) + len(marker)
    end = migration.index('"""', start)
    return migration[start:end]


def test_0005_backfill_sql_uses_asymmetric_retry_superseding_rules() -> None:
    migration = _migration_source()
    copy_sql = _constant_sql(migration, "COPY_UPLOAD_MODE_BACKFILL_SQL")
    overwrite_sql = _constant_sql(migration, "OVERWRITE_UPLOAD_MODE_BACKFILL_SQL")

    assert "SET upload_mode = 'copy'" in copy_sql
    assert "SET upload_mode = 'overwrite'" in overwrite_sql
    assert "'upload_retry'" not in copy_sql
    assert "'upload_retry'" in overwrite_sql
    assert "'upload_approve'" not in copy_sql
    assert "'upload_approve'" in overwrite_sql

    for sql in (copy_sql, overwrite_sql):
        assert "newer.request_id = ur.id" in sql
        assert "newer.id > al.id" in sql
        assert "AND NOT EXISTS" in sql
        assert "ur.status = 'failed'" in sql

    assert "ur.target_path <> ur.target_folder || ur.safe_filename" in copy_sql
    assert "ur.target_path = ur.target_folder || ur.safe_filename" in overwrite_sql
    assert "AND al.action IN ('upload_copy', 'upload_copy_path')" in copy_sql
    assert "AND al.action = 'upload_overwrite'" in overwrite_sql
    assert (
        "UPDATE upload_requests SET upload_mode = 'normal' WHERE upload_mode IS NULL" in migration
    )
    assert "Legacy retry reused target_path but called upload with overwrite=False" in migration


def test_0005_legacy_upload_mode_regression_scenarios() -> None:
    other_request_overwrite = {
        "id": 99,
        "request_id": 2,
        "action": "upload_overwrite",
        "created_at": "same timestamp",
    }

    overwrite_scenarios = [
        ([{"id": 1, "request_id": 1, "action": "upload_overwrite"}], "overwrite"),
        (
            [
                {"id": 1, "request_id": 1, "action": "upload_overwrite"},
                {"id": 2, "request_id": 1, "action": "upload_retry"},
            ],
            "normal",
        ),
        (
            [
                {"id": 1, "request_id": 1, "action": "upload_overwrite"},
                {"id": 2, "request_id": 1, "action": "upload_retry"},
                {"id": 3, "request_id": 1, "action": "upload_retry"},
            ],
            "normal",
        ),
        (
            [
                {"id": 1, "request_id": 1, "action": "upload_overwrite"},
                {"id": 2, "request_id": 1, "action": "upload_retry"},
                {"id": 3, "request_id": 1, "action": "upload_overwrite"},
            ],
            "overwrite",
        ),
        (
            [
                {"id": 1, "request_id": 1, "action": "upload_overwrite"},
                {"id": 2, "request_id": 1, "action": "upload_retry"},
                {"id": 3, "request_id": 1, "action": "upload_approve"},
            ],
            "normal",
        ),
        (
            [
                {"id": 1, "request_id": 1, "action": "upload_overwrite"},
                {"id": 2, "request_id": 1, "action": "upload_approve"},
            ],
            "normal",
        ),
        ([{"id": 1, "request_id": 1, "action": "upload_retry"}], "normal"),
        ([other_request_overwrite], "normal"),
        (
            [
                {"id": 10, "request_id": 1, "action": "upload_overwrite", "created_at": "same"},
                {"id": 11, "request_id": 1, "action": "upload_approve", "created_at": "same"},
            ],
            "normal",
        ),
        (
            [
                {"id": 1, "request_id": 2, "action": "upload_overwrite"},
                {"id": 2, "request_id": 2, "action": "upload_retry"},
                {"id": 3, "request_id": 1, "action": "upload_overwrite"},
            ],
            "overwrite",
        ),
    ]
    for audit_events, expected in overwrite_scenarios:
        assert _legacy_mode(audit_events=audit_events) == expected

    copy_scenarios = [
        ([{"id": 1, "request_id": 1, "action": "upload_copy"}], "copy"),
        (
            [
                {"id": 1, "request_id": 1, "action": "upload_copy"},
                {"id": 2, "request_id": 1, "action": "upload_retry"},
            ],
            "copy",
        ),
        (
            [
                {"id": 1, "request_id": 1, "action": "upload_copy_path"},
                {"id": 2, "request_id": 1, "action": "upload_retry"},
            ],
            "copy",
        ),
        (
            [
                {"id": 1, "request_id": 1, "action": "upload_copy_path"},
                {"id": 2, "request_id": 1, "action": "upload_approve"},
            ],
            "copy",
        ),
        (
            [
                {"id": 1, "request_id": 1, "action": "upload_copy"},
                {"id": 2, "request_id": 1, "action": "upload_retry"},
                {"id": 3, "request_id": 1, "action": "upload_retry"},
            ],
            "copy",
        ),
    ]
    for audit_events, expected in copy_scenarios:
        assert _legacy_mode(target_path="/copies/file.txt", audit_events=audit_events) == expected

    assert (
        _legacy_mode(
            audit_events=[
                {"id": 1, "request_id": 1, "action": "upload_copy"},
                {"id": 2, "request_id": 1, "action": "upload_overwrite"},
            ]
        )
        == "overwrite"
    )

    assert (
        _legacy_mode(
            target_path="/dst/file.txt",
            audit_events=[
                {"id": 1, "request_id": 1, "action": "upload_copy_path"},
                {"id": 2, "request_id": 1, "action": "upload_approve"},
            ],
        )
        == "normal"
    )


def _repair_migration_source() -> str:
    return Path("alembic/versions/0006_repair_upload_mode_backfill.py").read_text()


def test_0006_revision_repairs_upload_mode_after_0005() -> None:
    migration = _repair_migration_source()

    assert 'revision: str = "0006_repair_upload_mode_backfill"' in migration
    assert 'down_revision: str | None = "0005_upload_queue_worker"' in migration
    assert "WHERE ur.upload_mode IS NULL" not in migration
    assert "IS DISTINCT FROM classified.repaired_upload_mode" in migration
    assert "Data correction is intentionally irreversible" in migration


def test_0006_limits_repair_to_unqueued_legacy_failed_rows() -> None:
    sql = _constant_sql(_repair_migration_source(), "LEGACY_UPLOAD_MODE_REPAIR_SQL")

    for predicate in (
        "ur.status = 'failed'",
        "ur.attempt_count = 0",
        "ur.queued_at IS NULL",
        "ur.last_attempt_at IS NULL",
        "ur.worker_token IS NULL",
        "ur.lease_expires_at IS NULL",
    ):
        assert predicate in sql


def test_0006_repair_sql_preserves_final_legacy_classification_rules() -> None:
    sql = _constant_sql(_repair_migration_source(), "LEGACY_UPLOAD_MODE_REPAIR_SQL")

    assert "THEN 'copy'::uploadmode" in sql
    assert "THEN 'overwrite'::uploadmode" in sql
    assert "ELSE 'normal'::uploadmode" in sql
    assert "al.action IN ('upload_copy', 'upload_copy_path')" in sql
    assert "al.action = 'upload_overwrite'" in sql
    assert "newer.request_id = ur.id" in sql
    assert "newer.id > al.id" in sql
    assert "'upload_retry'" not in sql.split("THEN 'copy'::uploadmode", maxsplit=1)[0]
    assert "'upload_retry'" in sql.split("THEN 'copy'::uploadmode", maxsplit=1)[1]
    assert "'upload_approve'" not in sql.split("THEN 'copy'::uploadmode", maxsplit=1)[0]
    assert "'upload_approve'" in sql.split("THEN 'copy'::uploadmode", maxsplit=1)[1]


def test_0006_legacy_filter_semantics() -> None:
    def included(**overrides: object) -> bool:
        row = {
            "status": "failed",
            "attempt_count": 0,
            "queued_at": None,
            "last_attempt_at": None,
            "worker_token": None,
            "lease_expires_at": None,
        }
        row.update(overrides)
        return (
            row["status"] == "failed"
            and row["attempt_count"] == 0
            and row["queued_at"] is None
            and row["last_attempt_at"] is None
            and row["worker_token"] is None
            and row["lease_expires_at"] is None
        )

    assert included()
    assert not included(attempt_count=1)
    assert not included(queued_at="2026-07-11T00:00:00Z")
    assert not included(last_attempt_at="2026-07-11T00:00:00Z")
    assert not included(worker_token="token")
    assert not included(lease_expires_at="2026-07-11T00:00:00Z")
    assert not included(status="approved")
