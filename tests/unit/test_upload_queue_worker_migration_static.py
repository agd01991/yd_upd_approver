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
