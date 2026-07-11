from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from alembic import command

DATABASE_URL = os.getenv("MIGRATION_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not DATABASE_URL.startswith("postgresql"),
    reason="PostgreSQL migration regression requires MIGRATION_DATABASE_URL or DATABASE_URL",
)


def _sync_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg://")


@pytest.fixture()
def migration_db():
    db_url = _sync_url(DATABASE_URL)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL is unavailable: {exc}")

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
    command.downgrade(cfg, "base")
    yield cfg, engine
    command.downgrade(cfg, "base")
    engine.dispose()


def _seed(conn):
    conn.execute(
        text(
            """
            INSERT INTO users (id, telegram_id, status, allowed_folders)
            VALUES (1, 91001, 'active', '[]'::jsonb)
            ON CONFLICT (telegram_id) DO NOTHING
            """
        )
    )
    rows = [
        (1, "copy_retry", "/dst/file-copy.txt", "normal", 0, None),
        (2, "copy_path_approve", "/dst/file-copy-path.txt", "normal", 0, None),
        (3, "overwrite", "/dst/file.txt", "normal", 0, None),
        (4, "overwrite_retry", "/dst/file.txt", "overwrite", 0, None),
        (5, "overwrite_approve", "/dst/file.txt", "overwrite", 0, None),
        (6, "normal", "/dst/file.txt", "copy", 0, None),
        (7, "other_audit", "/dst/file.txt", "normal", 0, None),
        (8, "same_timestamp", "/dst/file.txt", "overwrite", 0, None),
        (9, "new_attempt", "/dst/file-copy.txt", "normal", 1, None),
        (10, "new_queued", "/dst/file-copy.txt", "normal", 0, datetime.now(UTC)),
        (99, "other_request", "/dst/file.txt", "normal", 0, None),
    ]
    for id_, code, target_path, mode, attempts, queued_at in rows:
        conn.execute(
            text(
                """
                INSERT INTO upload_requests (
                    id, request_code, user_id, source, telegram_file_id, original_filename,
                    safe_filename, size_bytes, sha256, local_path, target_folder, target_path,
                    status, upload_mode, attempt_count, queued_at
                ) VALUES (
                    :id, :code, 1, 'telegram', 'file', 'file.txt', 'file.txt', 1, 'sha',
                    '/tmp/file.txt', '/dst/', :target_path, 'failed', :mode, :attempts, :queued_at
                )
                """
            ),
            {
                "id": id_,
                "code": code,
                "target_path": target_path,
                "mode": mode,
                "attempts": attempts,
                "queued_at": queued_at,
            },
        )
    events = [
        (1, 1, "upload_copy"),
        (2, 1, "upload_retry"),
        (3, 2, "upload_copy_path"),
        (4, 2, "upload_approve"),
        (5, 3, "upload_overwrite"),
        (6, 4, "upload_overwrite"),
        (7, 4, "upload_retry"),
        (8, 5, "upload_overwrite"),
        (9, 5, "upload_approve"),
        (10, 7, "upload_retry"),
        (11, 99, "upload_overwrite"),
        (12, 8, "upload_overwrite"),
        (13, 8, "upload_approve"),
        (14, 9, "upload_copy"),
        (15, 10, "upload_copy"),
    ]
    for id_, request_id, action in events:
        conn.execute(
            text(
                """
                INSERT INTO audit_log (id, actor_telegram_id, action, request_id, user_id, old_value, new_value, created_at)
                VALUES (:id, 1, :action, :request_id, 1, '{}'::jsonb, '{}'::jsonb, '2026-07-11T00:00:00Z')
                """
            ),
            {"id": id_, "request_id": request_id, "action": action},
        )


def _modes(conn):
    return dict(
        conn.execute(
            text("SELECT request_code, upload_mode::text FROM upload_requests WHERE id < 90")
        ).all()
    )


def test_existing_0005_database_is_repaired_by_head(migration_db):
    cfg, engine = migration_db
    command.upgrade(cfg, "0005_upload_queue_worker")
    with engine.begin() as conn:
        _seed(conn)
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0005_upload_queue_worker"
        )
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0006_repair_upload_mode_backfill"
        )
        expected = _modes(conn)
    command.downgrade(cfg, "0005_upload_queue_worker")
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        assert _modes(conn) == expected
        assert expected == {
            "copy_retry": "copy",
            "copy_path_approve": "copy",
            "overwrite": "overwrite",
            "overwrite_retry": "normal",
            "overwrite_approve": "normal",
            "normal": "normal",
            "other_audit": "normal",
            "same_timestamp": "normal",
            "new_attempt": "normal",
            "new_queued": "normal",
        }


def _seed_legacy_0004(conn):
    conn.execute(
        text(
            """
            INSERT INTO users (id, telegram_id, status, allowed_folders)
            VALUES (1, 91001, 'active', '[]'::jsonb)
            """
        )
    )
    rows = [
        (1, "copy_retry", "/dst/file-copy.txt"),
        (2, "copy_path_approve", "/dst/file-copy-path.txt"),
        (3, "overwrite", "/dst/file.txt"),
        (4, "overwrite_retry", "/dst/file.txt"),
        (5, "overwrite_approve", "/dst/file.txt"),
        (6, "normal", "/dst/file.txt"),
        (7, "other_audit", "/dst/file.txt"),
        (8, "same_timestamp", "/dst/file.txt"),
        (99, "other_request", "/dst/file.txt"),
    ]
    for id_, code, target_path in rows:
        conn.execute(
            text(
                """
                INSERT INTO upload_requests (
                    id, request_code, user_id, source, telegram_file_id, original_filename,
                    safe_filename, size_bytes, sha256, local_path, target_folder, target_path,
                    status
                ) VALUES (
                    :id, :code, 1, 'telegram', 'file', 'file.txt', 'file.txt', 1, 'sha',
                    '/tmp/file.txt', '/dst/', :target_path, 'failed'
                )
                """
            ),
            {"id": id_, "code": code, "target_path": target_path},
        )
    events = [
        (1, 1, "upload_copy"),
        (2, 1, "upload_retry"),
        (3, 2, "upload_copy_path"),
        (4, 2, "upload_approve"),
        (5, 3, "upload_overwrite"),
        (6, 4, "upload_overwrite"),
        (7, 4, "upload_retry"),
        (8, 5, "upload_overwrite"),
        (9, 5, "upload_approve"),
        (10, 7, "upload_retry"),
        (11, 99, "upload_overwrite"),
        (12, 8, "upload_overwrite"),
        (13, 8, "upload_approve"),
    ]
    for id_, request_id, action in events:
        conn.execute(
            text(
                """
                INSERT INTO audit_log (id, actor_telegram_id, action, request_id, user_id, old_value, new_value, created_at)
                VALUES (:id, 1, :action, :request_id, 1, '{}'::jsonb, '{}'::jsonb, '2026-07-11T00:00:00Z')
                """
            ),
            {"id": id_, "request_id": request_id, "action": action},
        )


def test_clean_install_path_runs_0005_then_0006_to_same_modes(migration_db):
    cfg, engine = migration_db
    command.upgrade(cfg, "0004_user_folder_names")
    with engine.begin() as conn:
        _seed_legacy_0004(conn)
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0006_repair_upload_mode_backfill"
        )
        assert _modes(conn) == {
            "copy_retry": "copy",
            "copy_path_approve": "copy",
            "overwrite": "overwrite",
            "overwrite_retry": "normal",
            "overwrite_approve": "normal",
            "normal": "normal",
            "other_audit": "normal",
            "same_timestamp": "normal",
        }
