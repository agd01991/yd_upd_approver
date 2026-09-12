from pathlib import Path

from app.db.models import AuditLog, FolderRenameRequest, UploadRequest, User


def test_pagination_models_are_non_nullable_and_rename_index_matches_query() -> None:
    for model in (User, UploadRequest, AuditLog, FolderRenameRequest):
        assert model.__table__.c.created_at.nullable is False
    index = next(
        item
        for item in FolderRenameRequest.__table__.indexes
        if item.name == "ix_folder_rename_user_created_id"
    )
    assert tuple(column.name for column in index.columns) == ("user_id", "created_at", "id")


def test_revision_follows_current_head_and_documents_stable_null_backfill() -> None:
    source = Path("alembic/versions/0012_pagination_created_at.py").read_text()
    assert 'down_revision = "0011_upload_index_ownership"' in source
    assert "WHERE created_at IS NULL" in source
    assert "1970-01-01 00:00:00+00" in source
    assert "id * INTERVAL '1 microsecond'" in source
