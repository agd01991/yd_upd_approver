from pathlib import Path

MODE_CHANGING_ACTIONS = (
    "upload_approve",
    "upload_overwrite",
    "upload_copy",
    "upload_copy_path",
)


def _legacy_mode(
    *, target_path="/dst/file.txt", target_folder="/dst/", safe_filename="file.txt", audit_events=()
):
    latest = None
    for event in sorted(audit_events, key=lambda item: item["id"]):
        if event["request_id"] == 1 and event["action"] in MODE_CHANGING_ACTIONS:
            latest = event["action"]

    canonical_path = target_folder + safe_filename
    has_legacy_copy_marker = any(
        event["request_id"] == 1 and event["action"] in {"upload_copy", "upload_copy_path"}
        for event in audit_events
    )
    if target_path != canonical_path and has_legacy_copy_marker:
        return "copy"
    if target_path == canonical_path and latest == "upload_overwrite":
        return "overwrite"
    return "normal"


def _newer_action_lists(migration: str) -> list[str]:
    lists = []
    marker = "newer.action IN ("
    start = 0
    while True:
        idx = migration.find(marker, start)
        if idx == -1:
            return lists
        end = migration.index(")", idx)
        lists.append(migration[idx:end])
        start = end + 1


def test_0005_backfill_sql_uses_latest_relevant_upload_intent() -> None:
    migration = Path("alembic/versions/0005_upload_queue_worker.py").read_text()

    for action in MODE_CHANGING_ACTIONS:
        assert action in migration
    assert "'upload_retry'" not in "\n".join(_newer_action_lists(migration))
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

    scenarios = [
        ([{"id": 1, "request_id": 1, "action": "upload_overwrite"}], "overwrite"),
        (
            [
                {"id": 1, "request_id": 1, "action": "upload_overwrite"},
                {"id": 2, "request_id": 1, "action": "upload_retry"},
            ],
            "overwrite",
        ),
        (
            [
                {"id": 1, "request_id": 1, "action": "upload_overwrite"},
                {"id": 2, "request_id": 1, "action": "upload_retry"},
                {"id": 3, "request_id": 1, "action": "upload_retry"},
            ],
            "overwrite",
        ),
        ([{"id": 1, "request_id": 1, "action": "upload_retry"}], "normal"),
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
                {"id": 1, "request_id": 1, "action": "upload_copy"},
                {"id": 2, "request_id": 1, "action": "upload_retry"},
                {"id": 3, "request_id": 1, "action": "upload_overwrite"},
            ],
            "overwrite",
        ),
        ([other_request_overwrite], "normal"),
        (
            [
                {"id": 10, "request_id": 1, "action": "upload_overwrite", "created_at": "same"},
                {"id": 11, "request_id": 1, "action": "upload_approve", "created_at": "same"},
            ],
            "normal",
        ),
    ]
    for audit_events, expected in scenarios:
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
                {"id": 3, "request_id": 1, "action": "upload_copy"},
            ],
            "copy",
        ),
        (
            [
                {"id": 1, "request_id": 1, "action": "upload_overwrite"},
                {"id": 2, "request_id": 1, "action": "upload_retry"},
                {"id": 3, "request_id": 1, "action": "upload_copy"},
            ],
            "copy",
        ),
    ]
    for audit_events, expected in copy_scenarios:
        assert _legacy_mode(target_path="/copies/file.txt", audit_events=audit_events) == expected

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
