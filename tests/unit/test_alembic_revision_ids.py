from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_revision_ids_are_valid_static() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = [revision.revision for revision in script.walk_revisions()]

    assert revisions
    assert all(isinstance(revision, str) and revision for revision in revisions)
    assert all(len(revision) <= 32 for revision in revisions)
    assert len(revisions) == len(set(revisions))
    assert script.get_heads() == ["0008_telegram_outbox"]
