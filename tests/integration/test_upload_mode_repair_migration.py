from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command

CURRENT_HEAD_REVISION = "0010_upload_created_index"
TELEGRAM_OUTBOX_REVISION = "0008_telegram_outbox"

MIGRATION_DATABASE_URL = os.getenv("MIGRATION_DATABASE_URL")
APPLICATION_DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not MIGRATION_DATABASE_URL,
    reason="PostgreSQL migration regression requires destructive MIGRATION_DATABASE_URL",
)

T = TypeVar("T")
SYSTEM_DATABASES = {"postgres", "template0", "template1"}
SAFE_SUFFIXES = ("_test", "_tests")


def _run_async[T](awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)


def _database_name(url: str | None) -> str | None:
    if not url:
        return None
    return make_url(url).database


def _assert_safe_migration_url(migration_url: str, application_url: str | None) -> str:
    url = make_url(migration_url)
    if url.get_backend_name() != "postgresql":
        raise AssertionError("MIGRATION_DATABASE_URL must use the PostgreSQL backend")
    database = url.database
    if not database:
        raise AssertionError("MIGRATION_DATABASE_URL must include a database name")
    if database in SYSTEM_DATABASES:
        raise AssertionError(
            "MIGRATION_DATABASE_URL must not point at a PostgreSQL system database"
        )
    if database == _database_name(application_url):
        raise AssertionError("MIGRATION_DATABASE_URL must not use the application database name")
    if not (database.startswith("test_") or database.endswith(SAFE_SUFFIXES)):
        raise AssertionError(
            "MIGRATION_DATABASE_URL database name must start with 'test_' or end with '_test'/'_tests'"
        )
    return database


async def _assert_connected_to_expected_database(
    conn: AsyncConnection, expected_database: str
) -> None:
    actual_database = (await conn.execute(text("SELECT current_database()"))).scalar_one()
    if actual_database != expected_database:
        raise AssertionError(
            "MIGRATION_DATABASE_URL connected to an unexpected database: "
            f"expected {expected_database!r}, got {actual_database!r}"
        )


def _migration_config() -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["database_url_override"] = MIGRATION_DATABASE_URL
    cfg.attributes["configure_logging"] = False
    return cfg


async def _run_with_migration_engine[T](
    database_url: str,
    expected_database: str,
    callback: Callable[[AsyncConnection], Awaitable[T]],
) -> T:
    engine = create_async_engine(
        database_url,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    try:
        async with engine.begin() as conn:
            await _assert_connected_to_expected_database(conn, expected_database)
            return await callback(conn)
    finally:
        await engine.dispose()


def _with_migration_connection[T](
    expected_database: str, callback: Callable[[AsyncConnection], Awaitable[T]]
) -> T:
    assert MIGRATION_DATABASE_URL is not None
    return _run_async(
        _run_with_migration_engine(MIGRATION_DATABASE_URL, expected_database, callback)
    )


@pytest.fixture()
def migration_db():
    assert MIGRATION_DATABASE_URL is not None
    expected_database = _assert_safe_migration_url(MIGRATION_DATABASE_URL, APPLICATION_DATABASE_URL)

    async def check_connection(conn: AsyncConnection) -> None:
        await _assert_connected_to_expected_database(conn, expected_database)

    try:
        _with_migration_connection(expected_database, check_connection)
    except OperationalError as exc:
        raise AssertionError("MIGRATION_DATABASE_URL PostgreSQL database is unavailable") from exc

    cfg = _migration_config()
    command.downgrade(cfg, "base")
    try:
        yield cfg, expected_database
    finally:
        try:
            _with_migration_connection(expected_database, check_connection)
        finally:
            command.downgrade(cfg, "base")


async def _seed(conn: AsyncConnection) -> None:
    await conn.execute(
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
        await conn.execute(
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
    path_rows = [
        (20, "canonical_no_slash", "/dst", "/dst/file.txt", "copy"),
        (21, "canonical_multi_slash", "/dst///", "/dst/file.txt", "copy"),
        (22, "root_folder", "/", "/file.txt", "copy"),
        (23, "true_copy_no_slash", "/dst", "/copies/file.txt", "copy"),
        (24, "overwrite_no_slash", "/dst", "/dst/file.txt", "overwrite"),
        (25, "other_audit_no_slash", "/dst", "/dst/file.txt", "normal"),
    ]
    for id_, code, folder, target_path, mode in path_rows:
        await conn.execute(
            text(
                """
                INSERT INTO upload_requests (
                    id, request_code, user_id, source, telegram_file_id, original_filename,
                    safe_filename, size_bytes, sha256, local_path, target_folder, target_path,
                    status, upload_mode, attempt_count
                ) VALUES (
                    :id, :code, 1, 'telegram', 'file', 'file.txt', 'file.txt', 1, 'sha',
                    '/tmp/file.txt', :folder, :target_path, 'failed', :mode, 0
                )
                """
            ),
            {"id": id_, "code": code, "folder": folder, "target_path": target_path, "mode": mode},
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
        (20, 20, "upload_copy"),
        (21, 21, "upload_copy"),
        (22, 22, "upload_copy"),
        (23, 23, "upload_copy"),
        (24, 24, "upload_overwrite"),
        (25, 99, "upload_copy"),
    ]
    for id_, request_id, action in events:
        await conn.execute(
            text("""
                INSERT INTO audit_log (id, actor_telegram_id, action, request_id, user_id, old_value, new_value, created_at)
                VALUES (:id, 1, :action, :request_id, 1, '{}'::jsonb, '{}'::jsonb, '2026-07-11T00:00:00Z')
                """),
            {"id": id_, "request_id": request_id, "action": action},
        )


async def _modes(conn: AsyncConnection) -> dict[str, str]:
    rows = (
        await conn.execute(
            text("SELECT request_code, upload_mode::text FROM upload_requests WHERE id < 90")
        )
    ).all()
    return dict(rows)


async def _revision(conn: AsyncConnection) -> str | None:
    return (
        await conn.execute(text("SELECT version_num FROM alembic_version"))
    ).scalar_one_or_none()


async def _seed_legacy_0004(conn: AsyncConnection) -> None:
    await conn.execute(
        text("""
            INSERT INTO users (id, telegram_id, status, allowed_folders)
            VALUES (1, 91001, 'active', '[]'::jsonb)
            """)
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
        await conn.execute(
            text("""
                INSERT INTO upload_requests (
                    id, request_code, user_id, source, telegram_file_id, original_filename,
                    safe_filename, size_bytes, sha256, local_path, target_folder, target_path,
                    status
                ) VALUES (
                    :id, :code, 1, 'telegram', 'file', 'file.txt', 'file.txt', 1, 'sha',
                    '/tmp/file.txt', '/dst/', :target_path, 'failed'
                )
                """),
            {"id": id_, "code": code, "target_path": target_path},
        )
    path_rows = [
        (20, "canonical_no_slash", "/dst", "/dst/file.txt"),
        (21, "canonical_multi_slash", "/dst///", "/dst/file.txt"),
        (22, "root_folder", "/", "/file.txt"),
        (23, "true_copy_no_slash", "/dst", "/copies/file.txt"),
        (24, "overwrite_no_slash", "/dst", "/dst/file.txt"),
        (25, "other_audit_no_slash", "/dst", "/dst/file.txt"),
    ]
    for id_, code, folder, target_path in path_rows:
        await conn.execute(
            text("""
                INSERT INTO upload_requests (
                    id, request_code, user_id, source, telegram_file_id, original_filename,
                    safe_filename, size_bytes, sha256, local_path, target_folder, target_path,
                    status
                ) VALUES (
                    :id, :code, 1, 'telegram', 'file', 'file.txt', 'file.txt', 1, 'sha',
                    '/tmp/file.txt', :folder, :target_path, 'failed'
                )
                """),
            {"id": id_, "code": code, "folder": folder, "target_path": target_path},
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
        (20, 20, "upload_copy"),
        (21, 21, "upload_copy"),
        (22, 22, "upload_copy"),
        (23, 23, "upload_copy"),
        (24, 24, "upload_overwrite"),
        (25, 99, "upload_copy"),
    ]
    for id_, request_id, action in events:
        await conn.execute(
            text("""
                INSERT INTO audit_log (id, actor_telegram_id, action, request_id, user_id, old_value, new_value, created_at)
                VALUES (:id, 1, :action, :request_id, 1, '{}'::jsonb, '{}'::jsonb, '2026-07-11T00:00:00Z')
                """),
            {"id": id_, "request_id": request_id, "action": action},
        )


def test_existing_0005_database_is_repaired_by_head(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0005_upload_queue_worker")

    async def seed_and_check(conn: AsyncConnection) -> None:
        await _seed(conn)
        assert (
            await conn.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one() == "0005_upload_queue_worker"

    _with_migration_connection(expected_database, seed_and_check)
    command.upgrade(cfg, "head")

    async def check_head(conn: AsyncConnection) -> dict[str, str]:
        assert (
            await conn.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one() == CURRENT_HEAD_REVISION
        return await _modes(conn)

    expected = _with_migration_connection(expected_database, check_head)
    command.downgrade(cfg, "0005_upload_queue_worker")
    command.upgrade(cfg, "head")

    async def check_idempotent(conn: AsyncConnection) -> None:
        assert await _modes(conn) == expected

    _with_migration_connection(expected_database, check_idempotent)
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
        "canonical_no_slash": "normal",
        "canonical_multi_slash": "normal",
        "root_folder": "normal",
        "true_copy_no_slash": "copy",
        "overwrite_no_slash": "overwrite",
        "other_audit_no_slash": "normal",
    }


def test_clean_install_path_runs_0005_to_head_to_same_modes(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0004_user_folder_names")
    _with_migration_connection(expected_database, _seed_legacy_0004)
    command.upgrade(cfg, "head")

    async def check(conn: AsyncConnection) -> None:
        assert (
            await conn.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one() == CURRENT_HEAD_REVISION
        assert await _modes(conn) == {
            "copy_retry": "copy",
            "copy_path_approve": "copy",
            "overwrite": "overwrite",
            "overwrite_retry": "normal",
            "overwrite_approve": "normal",
            "normal": "normal",
            "other_audit": "normal",
            "same_timestamp": "normal",
            "canonical_no_slash": "normal",
            "canonical_multi_slash": "normal",
            "root_folder": "normal",
            "true_copy_no_slash": "copy",
            "overwrite_no_slash": "overwrite",
            "other_audit_no_slash": "normal",
        }

    _with_migration_connection(expected_database, check)


def test_existing_0009_database_receives_upload_ordering_index(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0009_db_integrity")

    async def indexes_at_0009(conn: AsyncConnection) -> None:
        assert await _revision(conn) == "0009_db_integrity"
        indexes = set(
            (
                await conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = current_schema() "
                        "AND tablename = 'upload_requests'"
                    )
                )
            ).scalars()
        )
        assert "ix_upload_requests_created_id" not in indexes
        assert {
            "ix_upload_requests_user_created_id",
            "ix_upload_requests_status_created_id",
        } <= indexes

    _with_migration_connection(expected_database, indexes_at_0009)
    command.upgrade(cfg, "head")

    async def index_at_head(conn: AsyncConnection) -> None:
        assert await _revision(conn) == CURRENT_HEAD_REVISION
        assert (
            await conn.execute(text("SELECT to_regclass('ix_upload_requests_created_id')"))
        ).scalar_one() == "ix_upload_requests_created_id"

    _with_migration_connection(expected_database, index_at_head)
    command.downgrade(cfg, "0009_db_integrity")

    async def index_removed_at_0009(conn: AsyncConnection) -> None:
        assert await _revision(conn) == "0009_db_integrity"
        assert (
            await conn.execute(text("SELECT to_regclass('ix_upload_requests_created_id')"))
        ).scalar_one() is None
        indexes = set(
            (
                await conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = current_schema() "
                        "AND tablename = 'upload_requests'"
                    )
                )
            ).scalars()
        )
        assert {
            "ix_upload_requests_user_created_id",
            "ix_upload_requests_status_created_id",
        } <= indexes
        assert (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conrelid = 'upload_requests'::regclass "
                    "AND conname = 'ck_upload_requests_attempt_count_non_negative'"
                )
            )
        ).scalar_one() == 1

    _with_migration_connection(expected_database, index_removed_at_0009)
    command.upgrade(cfg, "head")
    _with_migration_connection(expected_database, index_at_head)


async def _telegram_outbox_schema_state(conn: AsyncConnection) -> dict[str, bool]:
    row = (
        await conn.execute(
            text("""
                SELECT
                    to_regtype('telegramoutboxstatus') IS NOT NULL AS enum_exists,
                    to_regclass('telegram_outbox') IS NOT NULL AS table_exists,
                    EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                            AND table_name = 'telegram_outbox'
                            AND column_name = 'status'
                    ) AS status_column_exists
                """)
        )
    ).one()
    return dict(row._mapping)


def test_telegram_outbox_enum_migration_up_down_up(migration_db):
    cfg, expected_database = migration_db

    command.upgrade(cfg, TELEGRAM_OUTBOX_REVISION)

    async def check_created(conn: AsyncConnection) -> None:
        assert await _revision(conn) == TELEGRAM_OUTBOX_REVISION
        assert await _telegram_outbox_schema_state(conn) == {
            "enum_exists": True,
            "table_exists": True,
            "status_column_exists": True,
        }

    _with_migration_connection(expected_database, check_created)
    command.downgrade(cfg, "0007_repair_queued_retries")

    async def check_removed(conn: AsyncConnection) -> None:
        assert await _revision(conn) == "0007_repair_queued_retries"
        assert await _telegram_outbox_schema_state(conn) == {
            "enum_exists": False,
            "table_exists": False,
            "status_column_exists": False,
        }

    _with_migration_connection(expected_database, check_removed)
    command.upgrade(cfg, TELEGRAM_OUTBOX_REVISION)
    _with_migration_connection(expected_database, check_created)


def test_alembic_database_url_override_isolates_application_database(migration_db):
    cfg, expected_database = migration_db
    assert APPLICATION_DATABASE_URL is not None
    app_database = _database_name(APPLICATION_DATABASE_URL)
    migration_database = _database_name(MIGRATION_DATABASE_URL)
    assert app_database is not None
    assert app_database != migration_database

    async def app_revision(conn: AsyncConnection) -> str | None:
        return await _revision(conn)

    async def migration_revision(conn: AsyncConnection) -> str | None:
        return await _revision(conn)

    before = _run_async(
        _run_with_migration_engine(
            APPLICATION_DATABASE_URL,
            app_database,
            app_revision,
        )
    )
    command.upgrade(cfg, "0004_user_folder_names")
    assert (
        _with_migration_connection(expected_database, migration_revision)
        == "0004_user_folder_names"
    )
    after = _run_async(
        _run_with_migration_engine(
            APPLICATION_DATABASE_URL,
            app_database,
            app_revision,
        )
    )
    assert after == before


def test_programmatic_alembic_preserves_pytest_logging(migration_db, caplog):
    cfg, _expected_database = migration_db
    logger_name = "app.tests.alembic_logging_regression"
    logger = logging.getLogger(logger_name)
    marker = "alembic-preserved-pytest-logging-marker"

    assert logger.disabled is False
    with caplog.at_level(logging.INFO, logger=logger_name):
        command.upgrade(cfg, "head")
        assert logger.disabled is False
        logger.info(marker)

    assert marker in caplog.text


async def _seed_queued_legacy_retries(conn: AsyncConnection) -> None:
    await conn.execute(
        text("""
            INSERT INTO users (id, telegram_id, status, allowed_folders)
            VALUES (1, 91001, 'active', '[]'::jsonb)
            ON CONFLICT (telegram_id) DO NOTHING
            """)
    )
    queued_at = datetime(2026, 7, 13, tzinfo=UTC)
    modern_queued_at = datetime(2026, 7, 13, 3, tzinfo=UTC)
    lease = datetime(2026, 7, 13, 1, tzinfo=UTC)
    last_attempt = datetime(2026, 7, 13, 2, tzinfo=UTC)
    rows = [
        (
            99,
            "unrelated_request",
            "failed",
            "/dst/unrelated.txt",
            "normal",
            0,
            None,
            None,
            None,
            None,
        ),
        (
            101,
            "queued_overwrite_retry",
            "approved",
            "/dst/file.txt",
            "overwrite",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            102,
            "queued_copy_retry",
            "approved",
            "/dst/file-copy.txt",
            "overwrite",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            103,
            "queued_normal_retry",
            "approved",
            "/dst/file.txt",
            "overwrite",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            104,
            "explicit_overwrite",
            "approved",
            "/dst/file.txt",
            "overwrite",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            105,
            "explicit_copy",
            "approved",
            "/dst/file-copy.txt",
            "copy",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            106,
            "explicit_approve",
            "approved",
            "/dst/file.txt",
            "normal",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            107,
            "attempted",
            "approved",
            "/dst/file.txt",
            "overwrite",
            1,
            queued_at,
            None,
            None,
            None,
        ),
        (
            108,
            "uploading",
            "uploading",
            "/dst/file.txt",
            "overwrite",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            109,
            "worker_token",
            "approved",
            "/dst/file.txt",
            "overwrite",
            0,
            queued_at,
            None,
            "worker-1",
            None,
        ),
        (110, "leased", "approved", "/dst/file.txt", "overwrite", 0, queued_at, None, None, lease),
        (
            111,
            "last_attempt",
            "approved",
            "/dst/file.txt",
            "overwrite",
            0,
            queued_at,
            last_attempt,
            None,
            None,
        ),
        (
            112,
            "other_retry_only",
            "approved",
            "/dst/file.txt",
            "overwrite",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            113,
            "newer_after_retry",
            "approved",
            "/dst/file.txt",
            "overwrite",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            114,
            "uploaded_retry",
            "uploaded",
            "/dst/file.txt",
            "overwrite",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            115,
            "rejected_retry",
            "rejected",
            "/dst/file.txt",
            "overwrite",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            116,
            "legacy_null_metadata",
            "approved",
            "/dst/file.txt",
            "overwrite",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            117,
            "legacy_status_metadata",
            "approved",
            "/dst/file.txt",
            "overwrite",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            118,
            "modern_overwrite_retry",
            "approved",
            "/dst/file.txt",
            "overwrite",
            0,
            modern_queued_at,
            None,
            None,
            None,
        ),
        (
            119,
            "modern_copy_retry",
            "approved",
            "/dst/file-copy.txt",
            "copy",
            0,
            modern_queued_at,
            None,
            None,
            None,
        ),
        (
            120,
            "modern_normal_retry",
            "approved",
            "/dst/file.txt",
            "normal",
            0,
            modern_queued_at,
            None,
            None,
            None,
        ),
        (
            121,
            "modern_same_time_mode",
            "approved",
            "/dst/file.txt",
            "overwrite",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            122,
            "modern_same_time_queued",
            "approved",
            "/dst/file.txt",
            "overwrite",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            123,
            "queued_canonical_no_slash",
            "approved",
            "/dst/file.txt",
            "copy",
            0,
            queued_at,
            None,
            None,
            None,
        ),
        (
            124,
            "queued_true_copy_no_slash",
            "approved",
            "/copies/file.txt",
            "overwrite",
            0,
            queued_at,
            None,
            None,
            None,
        ),
    ]
    for row in rows:
        await conn.execute(
            text("""
                INSERT INTO upload_requests (
                    id, request_code, user_id, source, telegram_file_id, original_filename,
                    safe_filename, size_bytes, sha256, local_path, target_folder, target_path,
                    status, upload_mode, attempt_count, queued_at, approved_at, created_at,
                    last_attempt_at, worker_token, lease_expires_at
                ) VALUES (
                    :id, :code, 1, 'telegram', 'file', 'file.txt', 'file.txt', 1, 'sha',
                    '/tmp/file.txt', CASE WHEN :id IN (123, 124) THEN '/dst' ELSE '/dst/' END, :target_path, :status, :mode, :attempts,
                    :queued_at, :created_at, :created_at, :last_attempt_at,
                    :worker_token, :lease_expires_at
                )
                """),
            {
                "id": row[0],
                "code": row[1],
                "status": row[2],
                "target_path": row[3],
                "mode": row[4],
                "attempts": row[5],
                "queued_at": row[6],
                "created_at": queued_at,
                "last_attempt_at": row[7],
                "worker_token": row[8],
                "lease_expires_at": row[9],
            },
        )
    events = [
        (101, 101, "upload_overwrite"),
        (102, 101, "upload_retry"),
        (103, 102, "upload_copy"),
        (104, 102, "upload_retry"),
        (105, 103, "upload_approve"),
        (106, 103, "upload_retry"),
        (107, 104, "upload_retry"),
        (108, 104, "upload_overwrite"),
        (109, 105, "upload_retry"),
        (110, 105, "upload_copy"),
        (111, 106, "upload_retry"),
        (112, 106, "upload_approve"),
        (113, 107, "upload_overwrite"),
        (114, 107, "upload_retry"),
        (115, 108, "upload_overwrite"),
        (116, 108, "upload_retry"),
        (117, 109, "upload_overwrite"),
        (118, 109, "upload_retry"),
        (119, 110, "upload_overwrite"),
        (120, 110, "upload_retry"),
        (121, 111, "upload_overwrite"),
        (122, 111, "upload_retry"),
        (123, 99, "upload_retry"),
        (124, 113, "upload_retry"),
        (125, 113, "upload_approve"),
        (126, 114, "upload_overwrite"),
        (127, 114, "upload_retry"),
        (128, 115, "upload_overwrite", {}),
        (129, 115, "upload_retry", {}),
        (130, 116, "upload_retry", None),
        (131, 117, "upload_retry", {"status": "approved"}),
        (
            132,
            118,
            "upload_retry",
            {
                "status": "approved",
                "upload_mode": "overwrite",
                "queued_at": modern_queued_at.isoformat(),
            },
        ),
        (
            133,
            119,
            "upload_retry",
            {
                "status": "approved",
                "upload_mode": "copy",
                "queued_at": modern_queued_at.isoformat(),
            },
        ),
        (
            134,
            120,
            "upload_retry",
            {
                "status": "approved",
                "upload_mode": "normal",
                "queued_at": modern_queued_at.isoformat(),
            },
        ),
        (135, 121, "upload_retry", {"upload_mode": "overwrite"}),
        (136, 122, "upload_retry", {"queued_at": queued_at.isoformat()}),
        (137, 123, "upload_copy"),
        (138, 123, "upload_retry"),
        (139, 124, "upload_copy"),
        (140, 124, "upload_retry"),
    ]
    for event in events:
        id_, request_id, action, *metadata = event
        new_value = metadata[0] if metadata else {}
        await conn.execute(
            text("""
                INSERT INTO audit_log (id, actor_telegram_id, action, request_id, user_id, old_value, new_value, created_at)
                VALUES (:id, 1, :action, :request_id, 1, '{}'::jsonb, CAST(:new_value AS jsonb), '2026-07-13T00:00:00Z')
                """),
            {
                "id": id_,
                "request_id": request_id,
                "action": action,
                "new_value": None if new_value is None else json.dumps(new_value),
            },
        )


async def _queued_modes(conn: AsyncConnection) -> dict[str, str]:
    rows = (
        await conn.execute(
            text("""
                SELECT request_code, upload_mode::text
                FROM upload_requests
                WHERE id BETWEEN 101 AND 124
                ORDER BY id
                """)
        )
    ).all()
    return dict(rows)


async def _queued_stable_columns(conn: AsyncConnection) -> dict[str, tuple]:
    rows = (
        await conn.execute(
            text("""
                SELECT
                    request_code,
                    status::text,
                    queued_at,
                    approved_at,
                    attempt_count,
                    worker_token,
                    lease_expires_at,
                    last_attempt_at,
                    target_path
                FROM upload_requests
                WHERE id BETWEEN 101 AND 124
                ORDER BY id
                """)
        )
    ).all()
    return {row[0]: tuple(row[1:]) for row in rows}


def test_existing_0006_database_repairs_queued_legacy_retries(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0006_repair_upload_mode_backfill")

    async def seed_and_check(conn: AsyncConnection) -> dict[str, tuple]:
        await _seed_queued_legacy_retries(conn)
        assert await _revision(conn) == "0006_repair_upload_mode_backfill"
        return await _queued_stable_columns(conn)

    before_stable_columns = _with_migration_connection(expected_database, seed_and_check)
    command.upgrade(cfg, "head")

    async def check(conn: AsyncConnection) -> tuple[dict[str, str], dict[str, tuple]]:
        assert await _revision(conn) == CURRENT_HEAD_REVISION
        return await _queued_modes(conn), await _queued_stable_columns(conn)

    expected, after_stable_columns = _with_migration_connection(expected_database, check)
    assert after_stable_columns == before_stable_columns
    assert expected == {
        "queued_overwrite_retry": "normal",
        "queued_copy_retry": "copy",
        "queued_normal_retry": "normal",
        "legacy_null_metadata": "normal",
        "legacy_status_metadata": "normal",
        "explicit_overwrite": "overwrite",
        "explicit_copy": "copy",
        "explicit_approve": "normal",
        "attempted": "overwrite",
        "uploading": "overwrite",
        "worker_token": "overwrite",
        "leased": "overwrite",
        "last_attempt": "overwrite",
        "other_retry_only": "overwrite",
        "newer_after_retry": "overwrite",
        "uploaded_retry": "overwrite",
        "rejected_retry": "overwrite",
        "modern_overwrite_retry": "overwrite",
        "modern_copy_retry": "copy",
        "modern_normal_retry": "normal",
        "modern_same_time_mode": "overwrite",
        "modern_same_time_queued": "overwrite",
        "queued_canonical_no_slash": "normal",
        "queued_true_copy_no_slash": "copy",
    }

    command.downgrade(cfg, "0006_repair_upload_mode_backfill")
    command.upgrade(cfg, "head")

    async def check_idempotent(conn: AsyncConnection) -> None:
        assert await _revision(conn) == CURRENT_HEAD_REVISION
        assert await _queued_modes(conn) == expected

    _with_migration_connection(expected_database, check_idempotent)
