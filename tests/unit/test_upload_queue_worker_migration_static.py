from pathlib import Path


def test_0005_backfills_legacy_failed_copy_and_overwrite_modes() -> None:
    migration = Path("alembic/versions/0005_upload_queue_worker.py").read_text()
    assert "upload_copy_path" in migration
    assert "upload_copy" in migration
    assert "upload_overwrite" in migration
    assert "ur.status = 'failed'" in migration
    assert "ur.target_path <> ur.target_folder || ur.safe_filename" in migration
    assert "SET upload_mode = 'copy'" in migration
    assert "SET upload_mode = 'overwrite'" in migration
    assert (
        "UPDATE upload_requests SET upload_mode = 'normal' WHERE upload_mode IS NULL" in migration
    )
