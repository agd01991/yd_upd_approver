from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_revision_ids_are_valid_static() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = [revision.revision for revision in script.walk_revisions()]

    assert revisions
    assert all(isinstance(revision, str) and revision for revision in revisions)
    assert all(len(revision) <= 32 for revision in revisions)
    assert len(revisions) == len(set(revisions))
    assert script.get_heads() == ["0012_pagination_created_at"]
    assert (
        script.get_revision("0012_pagination_created_at").down_revision
        == "0011_upload_index_ownership"
    )
    assert (
        script.get_revision("0011_upload_index_ownership").down_revision
        == "0010_upload_created_index"
    )
