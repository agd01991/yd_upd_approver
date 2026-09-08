from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def _migration():
    return (
        ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0009_db_integrity").module
    )


def test_legacy_index_guard_runs_before_any_downgrade_mutation(monkeypatch) -> None:  # noqa: ANN001
    migration = _migration()
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(migration.op, "execute", lambda sql: calls.append(("execute", str(sql))))
    for name in ("drop_constraint", "create_foreign_key", "drop_index", "alter_column"):
        monkeypatch.setattr(
            migration.op,
            name,
            lambda *args, _name=name, **kwargs: calls.append((_name, str(args))),
        )

    migration.downgrade()

    assert calls[0][0] == "execute"
    assert "ix_upload_requests_created_id" in calls[0][1]
    assert "0011_upload_index_ownership" in calls[0][1]
    assert "0008_telegram_outbox" in calls[0][1]
    assert calls[1][0] == "drop_constraint"


def test_0009_offline_downgrade_contains_legacy_guard_before_schema_changes() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "downgrade",
            "0009_db_integrity:0008_telegram_outbox",
            "--sql",
        ],
        cwd=Path(__file__).parents[2],
        check=True,
        capture_output=True,
        text=True,
    )
    sql = result.stdout
    guard = sql.index("Cannot downgrade 0009_db_integrity while ix_upload_requests_created_id")
    assert guard < sql.index("ALTER TABLE")
    assert "pg_catalog.pg_index" in sql
    assert "0011_upload_index_ownership" in sql
    assert "0008_telegram_outbox" in sql
