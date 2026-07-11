from pathlib import Path

UPLOAD_INTENT_ACTIONS = (
    "upload_approve",
    "upload_retry",
    "upload_overwrite",
    "upload_copy",
    "upload_copy_path",
)


def _legacy_mode(
    *, target_path="/dst/file.txt", target_folder="/dst/", safe_filename="file.txt", audit_events=()
):
    latest = None
    for event in sorted(audit_events, key=lambda item: item["id"]):
        if event["request_id"] == 1 and event["action"] in UPLOAD_INTENT_ACTIONS:
            latest = event["action"]

    canonical_path = target_folder + safe_filename
    if target_path != canonical_path and latest in {"upload_copy", "upload_copy_path"}:
        return "copy"
    if target_path == canonical_path and latest == "upload_overwrite":
        return "overwrite"
    return "normal"


def test_0005_backfill_sql_uses_latest_relevant_upload_intent() -> None:
    migration = Path("alembic/versions/0005_upload_queue_worker.py").read_text()

    for action in UPLOAD_INTENT_ACTIONS:
        assert action in migration
    assert "newer.action IN" in migration
    assert "newer.id > al.id" in migration
    assert "AND NOT EXISTS" in migration
    assert "ur.status = 'failed'" in migration
    assert "ur.target_path <> ur.target_folder || ur.safe_filename" in migration
    assert "ur.target_path = ur.target_folder || ur.safe_filename" in migration
    assert "SET upload_mode = 'copy'" in migration
    assert "SET upload_mode = 'overwrite'" in migration
    assert (
        "UPDATE upload_requests SET upload_mode = 'normal' WHERE upload_mode IS NULL" in migration
    )


def test_0005_legacy_upload_mode_regression_scenarios() -> None:
    other_request_overwrite = {
        "id": 99,
        "request_id": 2,
        "action": "upload_overwrite",
        "created_at": "same timestamp",
    }

    assert (
        _legacy_mode(audit_events=[{"id": 1, "request_id": 1, "action": "upload_overwrite"}])
        == "overwrite"
    )
    assert (
        _legacy_mode(
            audit_events=[
                {"id": 1, "request_id": 1, "action": "upload_overwrite"},
                {"id": 2, "request_id": 1, "action": "upload_approve"},
            ]
        )
        == "normal"
    )
    assert (
        _legacy_mode(
            audit_events=[
                {"id": 1, "request_id": 1, "action": "upload_overwrite"},
                {"id": 2, "request_id": 1, "action": "upload_retry"},
            ]
        )
        == "normal"
    )
    assert (
        _legacy_mode(
            audit_events=[
                {"id": 1, "request_id": 1, "action": "upload_overwrite"},
                {"id": 2, "request_id": 1, "action": "upload_overwrite"},
            ]
        )
        == "overwrite"
    )
    assert (
        _legacy_mode(
            target_path="/copies/file.txt",
            audit_events=[{"id": 1, "request_id": 1, "action": "upload_copy_path"}],
        )
        == "copy"
    )
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
            target_path="/copies/file.txt",
            audit_events=[
                {"id": 1, "request_id": 1, "action": "upload_overwrite"},
                {"id": 2, "request_id": 1, "action": "upload_copy_path"},
            ],
        )
        == "copy"
    )
    assert _legacy_mode(audit_events=[]) == "normal"
    assert _legacy_mode(audit_events=[other_request_overwrite]) == "normal"
    assert (
        _legacy_mode(
            audit_events=[
                {
                    "id": 10,
                    "request_id": 1,
                    "action": "upload_overwrite",
                    "created_at": "same timestamp",
                },
                {
                    "id": 11,
                    "request_id": 1,
                    "action": "upload_approve",
                    "created_at": "same timestamp",
                },
            ]
        )
        == "normal"
    )
