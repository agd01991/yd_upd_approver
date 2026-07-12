from __future__ import annotations

import asyncio
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
        ).scalar_one() == "0006_repair_upload_mode_backfill"
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
    }


def test_clean_install_path_runs_0005_then_0006_to_same_modes(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0004_user_folder_names")
    _with_migration_connection(expected_database, _seed_legacy_0004)
    command.upgrade(cfg, "head")

    async def check(conn: AsyncConnection) -> None:
        assert (
            await conn.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one() == "0006_repair_upload_mode_backfill"
        assert await _modes(conn) == {
            "copy_retry": "copy",
            "copy_path_approve": "copy",
            "overwrite": "overwrite",
            "overwrite_retry": "normal",
            "overwrite_approve": "normal",
            "normal": "normal",
            "other_audit": "normal",
            "same_timestamp": "normal",
        }

    _with_migration_connection(expected_database, check)


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
