from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command

CURRENT_HEAD_REVISION = "0011_upload_index_ownership"
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
INDEX_OWNERSHIP_MARKER = "yd_upd_approver:alembic:0010_upload_created_index"


def _manual_qa_upload_index_sql(application_schema: str) -> str:
    manual_qa = Path("docs/MANUAL_QA.md").read_text()
    first_placeholder = manual_qa.index("REPLACE_WITH_APPLICATION_SCHEMA")
    placeholder_offset = manual_qa.index("REPLACE_WITH_APPLICATION_SCHEMA", first_placeholder + 1)
    query_start = manual_qa.rindex("```sql", 0, placeholder_offset) + len("```sql")
    query_end = manual_qa.index("```", placeholder_offset)
    return manual_qa[query_start:query_end].replace(
        "REPLACE_WITH_APPLICATION_SCHEMA", application_schema
    )


async def _restore_managed_upload_index(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_upload_requests_created_id "
            "ON public.upload_requests (created_at, id)"
        )
    )
    await conn.execute(
        text(
            "COMMENT ON INDEX public.ix_upload_requests_created_id IS "
            "'yd_upd_approver:alembic:0010_upload_created_index'"
        )
    )


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


@pytest.mark.parametrize("preexisting_index", [False, True], ids=["ordinary", "intermediate"])
def test_existing_0009_database_receives_upload_ordering_index(
    migration_db, preexisting_index: bool
):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0009_db_integrity")

    async def indexes_at_0009(conn: AsyncConnection) -> None:
        assert await _revision(conn) == "0009_db_integrity"
        indexes = set(
            (
                await conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = (SELECT namespace.nspname FROM pg_class AS table_class "
                        "JOIN pg_namespace AS namespace ON namespace.oid = table_class.relnamespace "
                        "WHERE table_class.oid = to_regclass('upload_requests')) "
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

    if preexisting_index:

        async def create_intermediate_index(conn: AsyncConnection) -> None:
            await conn.execute(
                text(
                    "CREATE INDEX ix_upload_requests_created_id ON upload_requests (created_at, id)"
                )
            )
            assert await _revision(conn) == "0009_db_integrity"

        _with_migration_connection(expected_database, create_intermediate_index)

    command.upgrade(cfg, "head")

    async def index_at_head(conn: AsyncConnection) -> None:
        assert await _revision(conn) == CURRENT_HEAD_REVISION
        index_columns = (
            await conn.execute(
                text(
                    "SELECT array_agg(attribute.attname ORDER BY key.ordinality) "
                    "FROM pg_class AS index_class "
                    "JOIN pg_index AS index_definition "
                    "ON index_definition.indexrelid = index_class.oid "
                    "CROSS JOIN LATERAL unnest(index_definition.indkey) "
                    "WITH ORDINALITY AS key(attnum, ordinality) "
                    "JOIN pg_attribute AS attribute "
                    "ON attribute.attrelid = index_definition.indrelid "
                    "AND attribute.attnum = key.attnum "
                    "WHERE index_class.relname = 'ix_upload_requests_created_id'"
                )
            )
        ).scalar_one()
        assert index_columns == ["created_at", "id"]
        assert (
            await conn.execute(
                text(
                    "SELECT obj_description('ix_upload_requests_created_id'::regclass, 'pg_class')"
                )
            )
        ).scalar_one() == INDEX_OWNERSHIP_MARKER
        assert (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_indexes "
                    "WHERE schemaname = (SELECT namespace.nspname FROM pg_class AS table_class "
                    "JOIN pg_namespace AS namespace ON namespace.oid = table_class.relnamespace "
                    "WHERE table_class.oid = to_regclass('upload_requests')) "
                    "AND tablename = 'upload_requests' "
                    "AND indexname = 'ix_upload_requests_created_id'"
                )
            )
        ).scalar_one() == 1

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
                        "WHERE schemaname = (SELECT namespace.nspname FROM pg_class AS table_class "
                        "JOIN pg_namespace AS namespace ON namespace.oid = table_class.relnamespace "
                        "WHERE table_class.oid = to_regclass('upload_requests')) "
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


def test_historical_0009_refuses_direct_downgrade_then_reconciles(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0009_db_integrity")

    async def recreate_historical_0009(conn: AsyncConnection) -> None:
        await conn.execute(
            text(
                "INSERT INTO users (id, telegram_id, status, allowed_folders) "
                "VALUES (9009, 99009, 'active', '[]'::jsonb)"
            )
        )
        # This is the exact index DDL used by the historical 0009 revision.
        await conn.execute(
            text("CREATE INDEX ix_upload_requests_created_id ON upload_requests (created_at, id)")
        )

    _with_migration_connection(expected_database, recreate_historical_0009)

    with pytest.raises(Exception, match="Upgrade through 0011_upload_index_ownership"):
        command.downgrade(cfg, TELEGRAM_OUTBOX_REVISION)

    async def unchanged_after_refusal(conn: AsyncConnection) -> None:
        assert await _revision(conn) == "0009_db_integrity"
        assert (
            await conn.execute(text("SELECT to_regclass('public.ix_upload_requests_created_id')"))
        ).scalar_one() is not None
        assert (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conrelid='upload_requests'::regclass "
                    "AND conname='ck_upload_requests_attempt_count_non_negative'"
                )
            )
        ).scalar_one() == 1
        assert (
            await conn.execute(text("SELECT count(*) FROM users WHERE id=9009"))
        ).scalar_one() == 1

    _with_migration_connection(expected_database, unchanged_after_refusal)
    command.upgrade(cfg, CURRENT_HEAD_REVISION)

    async def marked_at_head(conn: AsyncConnection) -> None:
        assert await _revision(conn) == CURRENT_HEAD_REVISION
        assert (
            await conn.execute(
                text(
                    "SELECT obj_description("
                    "'public.ix_upload_requests_created_id'::regclass, 'pg_class')"
                )
            )
        ).scalar_one() == INDEX_OWNERSHIP_MARKER

    _with_migration_connection(expected_database, marked_at_head)
    command.downgrade(cfg, TELEGRAM_OUTBOX_REVISION)

    async def clean_at_0008(conn: AsyncConnection) -> None:
        assert await _revision(conn) == TELEGRAM_OUTBOX_REVISION
        assert (
            await conn.execute(text("SELECT to_regclass('public.ix_upload_requests_created_id')"))
        ).scalar_one() is None
        assert (
            await conn.execute(text("SELECT count(*) FROM users WHERE id=9009"))
        ).scalar_one() == 1

    _with_migration_connection(expected_database, clean_at_0008)


def test_current_0009_downgrades_directly_without_ordering_index(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0009_db_integrity")
    command.downgrade(cfg, TELEGRAM_OUTBOX_REVISION)

    async def check(conn: AsyncConnection) -> None:
        assert await _revision(conn) == TELEGRAM_OUTBOX_REVISION
        assert (
            await conn.execute(text("SELECT to_regclass('public.ix_upload_requests_created_id')"))
        ).scalar_one() is None

    _with_migration_connection(expected_database, check)


def test_0009_downgrade_ignores_same_named_index_in_shadow_schema(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0009_db_integrity")

    async def create_shadow(conn: AsyncConnection) -> None:
        await conn.execute(text("CREATE SCHEMA qa_0009_shadow"))
        await conn.execute(
            text("CREATE TABLE qa_0009_shadow.upload_requests (created_at timestamptz, id integer)")
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_upload_requests_created_id "
                "ON qa_0009_shadow.upload_requests (created_at, id)"
            )
        )

    _with_migration_connection(expected_database, create_shadow)
    command.downgrade(cfg, TELEGRAM_OUTBOX_REVISION)

    async def check_shadow(conn: AsyncConnection) -> None:
        assert await _revision(conn) == TELEGRAM_OUTBOX_REVISION
        assert (
            await conn.execute(
                text("SELECT to_regclass('qa_0009_shadow.ix_upload_requests_created_id')")
            )
        ).scalar_one() is not None
        await conn.execute(text("DROP SCHEMA qa_0009_shadow CASCADE"))

    _with_migration_connection(expected_database, check_shadow)


@pytest.mark.parametrize("comment", [None, "foreign-owner"])
def test_0009_downgrade_refuses_conflicting_application_index(migration_db, comment: str | None):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0009_db_integrity")

    async def create_conflict(conn: AsyncConnection) -> None:
        await conn.execute(
            text("CREATE INDEX ix_upload_requests_created_id ON upload_requests (status, id)")
        )
        if comment is not None:
            await conn.execute(
                text("COMMENT ON INDEX public.ix_upload_requests_created_id IS :comment"),
                {"comment": comment},
            )

    _with_migration_connection(expected_database, create_conflict)
    with pytest.raises(Exception, match="0011_upload_index_ownership"):
        command.downgrade(cfg, TELEGRAM_OUTBOX_REVISION)

    async def unchanged(conn: AsyncConnection) -> None:
        assert await _revision(conn) == "0009_db_integrity"
        assert (
            await conn.execute(
                text(
                    "SELECT obj_description("
                    "'public.ix_upload_requests_created_id'::regclass, 'pg_class')"
                )
            )
        ).scalar_one() == comment

    _with_migration_connection(expected_database, unchanged)


@pytest.mark.parametrize(
    ("index_sql", "table_name", "columns"),
    [
        (
            "CREATE INDEX ix_upload_requests_created_id ON upload_requests (status, id)",
            "upload_requests",
            ["status", "id"],
        ),
        (
            "CREATE INDEX ix_upload_requests_created_id ON users (telegram_id)",
            "users",
            ["telegram_id"],
        ),
    ],
    ids=["wrong-columns", "wrong-table"],
)
def test_0010_rejects_conflicting_preexisting_upload_ordering_index(
    migration_db, index_sql: str, table_name: str, columns: list[str]
):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0009_db_integrity")

    async def create_conflicting_index(conn: AsyncConnection) -> None:
        await conn.execute(text(index_sql))
        assert await _revision(conn) == "0009_db_integrity"

    _with_migration_connection(expected_database, create_conflicting_index)

    with pytest.raises(RuntimeError, match="ix_upload_requests_created_id"):
        command.upgrade(cfg, "head")

    async def conflict_is_unchanged(conn: AsyncConnection) -> None:
        assert await _revision(conn) == "0009_db_integrity"
        row = (
            await conn.execute(
                text(
                    """
                    SELECT table_class.relname, array_agg(attribute.attname ORDER BY key.ordinality)
                    FROM pg_class AS index_class
                    JOIN pg_index AS index_definition
                        ON index_definition.indexrelid = index_class.oid
                    JOIN pg_class AS table_class ON table_class.oid = index_definition.indrelid
                    CROSS JOIN LATERAL unnest(index_definition.indkey)
                        WITH ORDINALITY AS key(attnum, ordinality)
                    JOIN pg_attribute AS attribute
                        ON attribute.attrelid = index_definition.indrelid
                        AND attribute.attnum = key.attnum
                    WHERE index_class.relnamespace = (
                        SELECT table_class.relnamespace
                        FROM pg_class AS table_class
                        WHERE table_class.oid = to_regclass('upload_requests')
                    )
                        AND index_class.relname = 'ix_upload_requests_created_id'
                    GROUP BY table_class.relname
                    """
                )
            )
        ).one()
        assert row[0] == table_name
        assert row[1] == columns

    _with_migration_connection(expected_database, conflict_is_unchanged)


def test_manual_qa_upload_index_query_ignores_shadow_search_path(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, CURRENT_HEAD_REVISION)

    async def check(conn: AsyncConnection) -> None:
        original_search_path = (
            await conn.execute(text("SELECT pg_catalog.current_setting('search_path')"))
        ).scalar_one()
        await conn.execute(text("CREATE SCHEMA qa_shadow"))
        try:
            await conn.execute(
                text(
                    "CREATE TABLE qa_shadow.upload_requests ("
                    "id bigint NOT NULL, created_at timestamptz NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX ix_upload_requests_created_id "
                    "ON qa_shadow.upload_requests (created_at, id)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE qa_shadow.pg_namespace ("
                    "oid pg_catalog.oid, nspname pg_catalog.name)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO qa_shadow.pg_namespace (oid, nspname) "
                    "VALUES (0, 'shadow namespace')"
                )
            )
            await conn.execute(
                text(
                    "CREATE FUNCTION qa_shadow.pg_get_indexdef(pg_catalog.oid) RETURNS text "
                    "LANGUAGE sql IMMUTABLE AS $$ SELECT 'shadow index definition'::text $$"
                )
            )
            await conn.execute(
                text(
                    "CREATE FUNCTION qa_shadow.obj_description("
                    "pg_catalog.oid, pg_catalog.name) RETURNS text "
                    "LANGUAGE sql IMMUTABLE AS $$ SELECT 'shadow ownership comment'::text $$"
                )
            )
            for function_name, left_type, right_type in (
                ("oid_equals", "pg_catalog.oid", "pg_catalog.oid"),
                ("name_equals", "pg_catalog.name", "pg_catalog.name"),
                ("int8_less_than_or_equals_int2", "pg_catalog.int8", "pg_catalog.int2"),
            ):
                await conn.execute(
                    text(
                        f"CREATE FUNCTION qa_shadow.{function_name}({left_type}, {right_type}) "
                        "RETURNS pg_catalog.bool LANGUAGE sql IMMUTABLE "
                        "AS $$ SELECT false::pg_catalog.bool $$"
                    )
                )
            await conn.execute(
                text(
                    "CREATE OPERATOR qa_shadow.= (LEFTARG = pg_catalog.oid, "
                    "RIGHTARG = pg_catalog.oid, FUNCTION = qa_shadow.oid_equals)"
                )
            )
            await conn.execute(
                text(
                    "CREATE OPERATOR qa_shadow.= (LEFTARG = pg_catalog.name, "
                    "RIGHTARG = pg_catalog.name, FUNCTION = qa_shadow.name_equals)"
                )
            )
            await conn.execute(
                text(
                    "CREATE OPERATOR qa_shadow.<= (LEFTARG = pg_catalog.int8, "
                    "RIGHTARG = pg_catalog.int2, "
                    "FUNCTION = qa_shadow.int8_less_than_or_equals_int2)"
                )
            )
            shadow_table_oid = (
                await conn.execute(text("SELECT 'qa_shadow.upload_requests'::regclass::oid"))
            ).scalar_one()
            shadow_index_oid = (
                await conn.execute(
                    text("SELECT 'qa_shadow.ix_upload_requests_created_id'::regclass::oid")
                )
            ).scalar_one()
            expected_search_path = "qa_shadow, pg_catalog, public"
            await conn.execute(text(f"SET SESSION search_path TO {expected_search_path}"))
            assert (
                await conn.execute(text("SELECT pg_catalog.current_setting('search_path')"))
            ).scalar_one() == expected_search_path

            # These probes are intentionally not pg_catalog-qualified: they prove that
            # the hostile session search_path resolves shadow relations and functions.
            assert (
                await conn.execute(text("SELECT nspname FROM pg_namespace"))
            ).scalar_one() == "shadow namespace"
            assert (
                await conn.execute(text("SELECT pg_get_indexdef(0::pg_catalog.oid)"))
            ).scalar_one() == "shadow index definition"
            assert (
                await conn.execute(
                    text("SELECT obj_description(0::pg_catalog.oid, 'pg_class'::pg_catalog.name)")
                )
            ).scalar_one() == "shadow ownership comment"

            # The unqualified operators in the negative probes are intentional: these
            # pairs prove that hostile operator lookup is active and catalog qualification
            # changes the result.
            assert not (
                await conn.execute(text("SELECT 1::pg_catalog.oid = 1::pg_catalog.oid"))
            ).scalar_one()
            assert (
                await conn.execute(
                    text("SELECT 1::pg_catalog.oid OPERATOR(pg_catalog.=) 1::pg_catalog.oid")
                )
            ).scalar_one()
            assert not (
                await conn.execute(text("SELECT 1::pg_catalog.int8 <= 1::pg_catalog.int2"))
            ).scalar_one()
            assert (
                await conn.execute(
                    text("SELECT 1::pg_catalog.int8 OPERATOR(pg_catalog.<=) 1::pg_catalog.int2")
                )
            ).scalar_one()
            assert not (
                await conn.execute(text("SELECT 'same'::pg_catalog.name = 'same'::pg_catalog.name"))
            ).scalar_one()
            assert (
                await conn.execute(
                    text(
                        "SELECT 'same'::pg_catalog.name OPERATOR(pg_catalog.=) "
                        "'same'::pg_catalog.name"
                    )
                )
            ).scalar_one()

            rows = (await conn.execute(text(_manual_qa_upload_index_sql("public")))).all()
            assert len(rows) == 1
            row = rows[0]
            assert row.application_schema == "public"
            assert row.table_oid != shadow_table_oid
            assert row.index_oid != shadow_index_oid
            assert row.index_name == "ix_upload_requests_created_id"
            assert list(row.key_columns) == ["created_at", "id"]
            assert "CREATE INDEX ix_upload_requests_created_id" in row.index_definition
            assert "shadow index definition" not in row.index_definition
            assert row.ownership_comment == INDEX_OWNERSHIP_MARKER

            assert (await conn.execute(text(_manual_qa_upload_index_sql("qa_missing")))).all() == []
            assert (
                await conn.execute(
                    text(
                        "SELECT c.oid, pg_catalog.obj_description(c.oid, 'pg_class') "
                        "FROM pg_catalog.pg_class AS c "
                        "JOIN pg_catalog.pg_namespace AS n "
                        "ON n.oid OPERATOR(pg_catalog.=) c.relnamespace "
                        "WHERE n.nspname OPERATOR(pg_catalog.=) 'qa_shadow'::pg_catalog.name "
                        "AND (c.relname OPERATOR(pg_catalog.=) "
                        "'upload_requests'::pg_catalog.name OR "
                        "c.relname OPERATOR(pg_catalog.=) "
                        "'ix_upload_requests_created_id'::pg_catalog.name) ORDER BY c.oid"
                    )
                )
            ).all() == [(shadow_table_oid, None), (shadow_index_oid, None)]
        finally:
            try:
                await conn.execute(
                    text("SELECT pg_catalog.set_config('search_path', :path, false)"),
                    {"path": original_search_path},
                )
                assert (
                    await conn.execute(text("SELECT pg_catalog.current_setting('search_path')"))
                ).scalar_one() == original_search_path
            finally:
                await conn.execute(text("DROP SCHEMA qa_shadow CASCADE"))

    _with_migration_connection(expected_database, check)


@pytest.mark.parametrize("scenario", ["existing", "shadow", "conflict"])
def test_0010_resolves_upload_index_schema_from_target_relation(migration_db, scenario: str):
    """A shadow first search_path entry must not change 0010's target schema."""
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0009_db_integrity")
    quoted_database = expected_database.replace('"', '""')

    async def prepare(conn: AsyncConnection) -> None:
        await conn.execute(text("CREATE SCHEMA role_shadow"))
        if scenario == "existing":
            await conn.execute(
                text(
                    "CREATE INDEX ix_upload_requests_created_id ON upload_requests (created_at, id)"
                )
            )
        elif scenario == "shadow":
            await conn.execute(
                text(
                    "CREATE TABLE role_shadow.shadow_upload_requests ("
                    "id bigint NOT NULL, created_at timestamptz NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX ix_upload_requests_created_id "
                    "ON role_shadow.shadow_upload_requests (created_at, id)"
                )
            )
        else:
            await conn.execute(
                text("CREATE INDEX ix_upload_requests_created_id ON users (telegram_id)")
            )
        await conn.execute(
            text(f'ALTER DATABASE "{quoted_database}" SET search_path TO role_shadow, public')
        )

    try:
        _with_migration_connection(expected_database, prepare)

        async def check_resolution(conn: AsyncConnection) -> None:
            assert (
                await conn.execute(text("SELECT current_schema()"))
            ).scalar_one() == "role_shadow"
            target_oid = (
                await conn.execute(text("SELECT to_regclass('upload_requests')::oid"))
            ).scalar_one()
            public_target_oid = (
                await conn.execute(text("SELECT 'public.upload_requests'::regclass::oid"))
            ).scalar_one()
            assert target_oid == public_target_oid
            if scenario == "shadow":
                rows = (
                    await conn.execute(
                        text(
                            """
                            SELECT index_namespace.nspname, table_namespace.nspname,
                                   table_class.relname, index_definition.indrelid
                            FROM pg_class AS index_class
                            JOIN pg_namespace AS index_namespace
                                ON index_namespace.oid = index_class.relnamespace
                            JOIN pg_index AS index_definition
                                ON index_definition.indexrelid = index_class.oid
                            JOIN pg_class AS table_class
                                ON table_class.oid = index_definition.indrelid
                            JOIN pg_namespace AS table_namespace
                                ON table_namespace.oid = table_class.relnamespace
                            WHERE index_class.relname = 'ix_upload_requests_created_id'
                            ORDER BY index_namespace.nspname
                            """
                        )
                    )
                ).all()
                shadow_target_oid = (
                    await conn.execute(
                        text("SELECT 'role_shadow.shadow_upload_requests'::regclass::oid")
                    )
                ).scalar_one()
                assert rows == [
                    ("role_shadow", "role_shadow", "shadow_upload_requests", shadow_target_oid)
                ]
                assert public_target_oid not in [row[3] for row in rows]

        _with_migration_connection(expected_database, check_resolution)
        if scenario == "conflict":
            with pytest.raises(RuntimeError, match="ix_upload_requests_created_id"):
                command.upgrade(cfg, "head")
        else:
            command.upgrade(cfg, "head")

        async def check_result(conn: AsyncConnection) -> None:
            expected_revision = (
                "0009_db_integrity" if scenario == "conflict" else CURRENT_HEAD_REVISION
            )
            assert await _revision(conn) == expected_revision
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT index_namespace.nspname, table_namespace.nspname,
                               table_class.relname, index_definition.indrelid
                        FROM pg_class AS index_class
                        JOIN pg_namespace AS index_namespace
                            ON index_namespace.oid = index_class.relnamespace
                        JOIN pg_index AS index_definition ON index_definition.indexrelid = index_class.oid
                        JOIN pg_class AS table_class ON table_class.oid = index_definition.indrelid
                        JOIN pg_namespace AS table_namespace
                            ON table_namespace.oid = table_class.relnamespace
                        WHERE index_class.relname = 'ix_upload_requests_created_id'
                        ORDER BY index_namespace.nspname
                        """
                    )
                )
            ).all()
            public_target_oid = (
                await conn.execute(text("SELECT 'public.upload_requests'::regclass::oid"))
            ).scalar_one()
            if scenario == "conflict":
                users_oid = (
                    await conn.execute(text("SELECT 'public.users'::regclass::oid"))
                ).scalar_one()
                assert rows == [("public", "public", "users", users_oid)]
            elif scenario == "shadow":
                shadow_target_oid = (
                    await conn.execute(
                        text("SELECT 'role_shadow.shadow_upload_requests'::regclass::oid")
                    )
                ).scalar_one()
                assert rows == [
                    ("public", "public", "upload_requests", public_target_oid),
                    ("role_shadow", "role_shadow", "shadow_upload_requests", shadow_target_oid),
                ]
            else:
                assert rows == [("public", "public", "upload_requests", public_target_oid)]

        _with_migration_connection(expected_database, check_result)
    finally:

        async def cleanup(conn: AsyncConnection) -> None:
            await conn.execute(text(f'ALTER DATABASE "{quoted_database}" RESET search_path'))
            await conn.execute(text("DROP SCHEMA IF EXISTS role_shadow CASCADE"))

        _with_migration_connection(expected_database, cleanup)


@pytest.mark.parametrize(
    "scenario",
    [
        "shadow-conflict",
        "wrong-desc",
        "wrong-nulls-first",
        "ambiguous",
        "unowned-compatible",
        "missing",
        "materialized",
        "materialized-only",
    ],
    ids=[
        "shadow",
        "wrong-desc",
        "wrong-nulls-first",
        "ambiguous",
        "unowned-compatible",
        "missing",
        "materialized",
        "materialized-only",
    ],
)
def test_0010_downgrade_finds_only_the_managed_index_across_schemas(migration_db, scenario: str):
    """Downgrade must not resolve upload_requests again through the changed search_path."""
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0010_upload_created_index")
    quoted_database = expected_database.replace('"', '""')

    async def prepare(conn: AsyncConnection) -> None:
        await conn.execute(text("CREATE SCHEMA role_shadow"))
        if scenario.startswith("materialized"):
            await conn.execute(
                text(
                    "CREATE MATERIALIZED VIEW role_shadow.upload_requests AS "
                    "SELECT 1::bigint AS id, now() AS created_at"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX ix_upload_requests_created_id "
                    "ON role_shadow.upload_requests (created_at, id)"
                )
            )
        else:
            await conn.execute(
                text(
                    "CREATE TABLE role_shadow.upload_requests "
                    "(id bigint NOT NULL, created_at timestamptz NOT NULL, "
                    "status text NOT NULL DEFAULT '')"
                )
            )
        if scenario == "shadow-conflict":
            await conn.execute(
                text(
                    "CREATE INDEX ix_upload_requests_created_id "
                    "ON role_shadow.upload_requests (status, id)"
                )
            )
        elif scenario in {"wrong-desc", "wrong-nulls-first"}:
            ordering = (
                "created_at DESC NULLS LAST, id ASC"
                if scenario == "wrong-desc"
                else "created_at ASC NULLS FIRST, id ASC"
            )
            await conn.execute(
                text(
                    "CREATE INDEX ix_upload_requests_created_id "
                    f"ON role_shadow.upload_requests ({ordering})"
                )
            )
        elif scenario in {"ambiguous", "unowned-compatible"}:
            await conn.execute(
                text(
                    "CREATE INDEX ix_upload_requests_created_id "
                    "ON role_shadow.upload_requests (created_at, id)"
                )
            )
            if scenario == "ambiguous":
                await conn.execute(
                    text(
                        "COMMENT ON INDEX role_shadow.ix_upload_requests_created_id IS "
                        "'yd_upd_approver:alembic:0010_upload_created_index'"
                    )
                )
        elif scenario in {"missing", "materialized-only"}:
            await conn.execute(text("DROP INDEX public.ix_upload_requests_created_id"))
            if scenario == "missing":
                await conn.execute(
                    text(
                        "CREATE INDEX ix_upload_requests_created_id "
                        "ON role_shadow.upload_requests (status, id)"
                    )
                )
        await conn.execute(
            text(f'ALTER DATABASE "{quoted_database}" SET search_path TO role_shadow, public')
        )

    try:
        _with_migration_connection(expected_database, prepare)

        async def shadow_is_resolved(conn: AsyncConnection) -> None:
            assert (
                await conn.execute(text("SELECT to_regclass('upload_requests')::oid"))
            ).scalar_one() == (
                await conn.execute(text("SELECT 'role_shadow.upload_requests'::regclass::oid"))
            ).scalar_one()

        _with_migration_connection(expected_database, shadow_is_resolved)
        if scenario == "ambiguous":
            with pytest.raises(RuntimeError, match="ambiguous compatible indexes"):
                command.downgrade(cfg, "0009_db_integrity")
        elif scenario in {"missing", "materialized-only"}:
            with pytest.raises(RuntimeError, match="no compatible managed index"):
                command.downgrade(cfg, "0009_db_integrity")
        else:
            command.downgrade(cfg, "0009_db_integrity")

        async def check(conn: AsyncConnection) -> None:
            shadow_index = (
                await conn.execute(
                    text("SELECT to_regclass('role_shadow.ix_upload_requests_created_id')")
                )
            ).scalar_one()
            public_index = (
                await conn.execute(
                    text("SELECT to_regclass('public.ix_upload_requests_created_id')")
                )
            ).scalar_one()
            if scenario in {
                "shadow-conflict",
                "wrong-desc",
                "wrong-nulls-first",
                "materialized",
                "unowned-compatible",
            }:
                assert await _revision(conn) == "0009_db_integrity"
                assert public_index is None
                assert shadow_index is not None
            else:
                assert await _revision(conn) == "0010_upload_created_index"
                assert shadow_index is not None
                assert (
                    public_index is None
                    if scenario in {"missing", "materialized-only"}
                    else public_index is not None
                )

        _with_migration_connection(expected_database, check)
    finally:

        async def cleanup(conn: AsyncConnection) -> None:
            await conn.execute(text(f'ALTER DATABASE "{quoted_database}" RESET search_path'))
            await conn.execute(text("SET search_path TO public"))
            await conn.execute(text("DROP SCHEMA IF EXISTS role_shadow CASCADE"))
            if (
                scenario in {"missing", "materialized-only"}
                and await _revision(conn) == "0010_upload_created_index"
            ):
                await _restore_managed_upload_index(conn)

        _with_migration_connection(expected_database, cleanup)


@pytest.mark.parametrize(
    "ordering",
    ["created_at DESC NULLS LAST, id ASC", "created_at ASC NULLS FIRST, id ASC"],
    ids=["desc", "nulls-first"],
)
def test_0010_upgrade_rejects_existing_index_with_wrong_order(migration_db, ordering: str):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0009_db_integrity")

    async def prepare(conn: AsyncConnection) -> None:
        await conn.execute(
            text(f"CREATE INDEX ix_upload_requests_created_id ON upload_requests ({ordering})")
        )

    _with_migration_connection(expected_database, prepare)

    with pytest.raises(RuntimeError, match="unexpected definition"):
        command.upgrade(cfg, CURRENT_HEAD_REVISION)

    async def check(conn: AsyncConnection) -> None:
        assert await _revision(conn) == "0009_db_integrity"
        options = (
            await conn.execute(
                text(
                    "SELECT indoption::smallint[] FROM pg_index "
                    "WHERE indexrelid = 'ix_upload_requests_created_id'::regclass"
                )
            )
        ).scalar_one()
        assert tuple(options) != (0, 0)

    _with_migration_connection(expected_database, check)


@pytest.mark.parametrize(
    "ordering",
    ["created_at DESC NULLS LAST, id ASC", "created_at ASC NULLS FIRST, id ASC"],
    ids=["desc", "nulls-first"],
)
def test_0010_downgrade_rejects_only_wrong_order_candidate(migration_db, ordering: str):
    cfg, expected_database = migration_db
    command.upgrade(cfg, CURRENT_HEAD_REVISION)

    async def prepare(conn: AsyncConnection) -> None:
        await conn.execute(text("CREATE SCHEMA role_shadow"))
        await conn.execute(
            text(
                "CREATE TABLE role_shadow.upload_requests "
                "(id bigint NOT NULL, created_at timestamptz NOT NULL)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_upload_requests_created_id "
                f"ON role_shadow.upload_requests ({ordering})"
            )
        )
        await conn.execute(text("DROP INDEX public.ix_upload_requests_created_id"))

    _with_migration_connection(expected_database, prepare)
    try:
        with pytest.raises(RuntimeError, match="managed index not found"):
            command.downgrade(cfg, "0009_db_integrity")

        async def check(conn: AsyncConnection) -> None:
            assert await _revision(conn) == CURRENT_HEAD_REVISION
            assert (
                await conn.execute(
                    text("SELECT to_regclass('role_shadow.ix_upload_requests_created_id')")
                )
            ).scalar_one() is not None

        _with_migration_connection(expected_database, check)
    finally:

        async def cleanup(conn: AsyncConnection) -> None:
            await conn.execute(text("DROP SCHEMA IF EXISTS role_shadow CASCADE"))
            await _restore_managed_upload_index(conn)

        _with_migration_connection(expected_database, cleanup)


def test_0010_offline_runtime_rejects_wrong_order_indexes(migration_db):
    cfg, expected_database = migration_db
    migration = ScriptDirectory.from_config(cfg).get_revision("0010_upload_created_index").module
    command.upgrade(cfg, "0009_db_integrity")

    async def check_upgrade(conn: AsyncConnection) -> None:
        await conn.execute(
            text(
                "CREATE INDEX ix_upload_requests_created_id "
                "ON public.upload_requests (created_at DESC NULLS LAST, id ASC)"
            )
        )
        with pytest.raises(Exception, match="incompatible signature"):
            await conn.execute(text(migration._offline_upgrade_sql()))
        await conn.execute(text("DROP INDEX public.ix_upload_requests_created_id"))

        await conn.execute(text("CREATE SCHEMA role_shadow"))
        await conn.execute(
            text(
                "CREATE TABLE role_shadow.upload_requests "
                "(id bigint NOT NULL, created_at timestamptz NOT NULL)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_upload_requests_created_id "
                "ON role_shadow.upload_requests (created_at ASC NULLS FIRST, id ASC)"
            )
        )
        with pytest.raises(Exception, match="no compatible managed index"):
            await conn.execute(text(migration._offline_downgrade_sql()))
        assert (
            await conn.execute(
                text("SELECT to_regclass('role_shadow.ix_upload_requests_created_id')")
            )
        ).scalar_one() is not None

    try:
        _with_migration_connection(expected_database, check_upgrade)
    finally:

        async def cleanup(conn: AsyncConnection) -> None:
            await conn.execute(text("DROP SCHEMA IF EXISTS role_shadow CASCADE"))
            await conn.execute(text("DROP INDEX IF EXISTS public.ix_upload_requests_created_id"))

        _with_migration_connection(expected_database, cleanup)


@pytest.mark.parametrize("execution", ["online", "offline-0010-to-0011"])
def test_anchor_fingerprint_ignores_name_only_shadow_target(migration_db, execution: str):
    """A perfect shadow fingerprint using a different enum OID is rejected."""
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0009_db_integrity")
    quoted_database = expected_database.replace('"', '""')

    async def prepare(conn: AsyncConnection) -> None:
        await conn.execute(text("CREATE SCHEMA anchor_shadow"))
        await conn.execute(
            text(
                "CREATE TYPE anchor_shadow.shadow_uploadstatus AS ENUM "
                "('new','stored','pending_approval','approved','uploading','uploaded',"
                "'rejected','failed','cancelled','deleted_temp')"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE anchor_shadow.upload_requests (id integer NOT NULL, "
                "user_id integer NOT NULL, status anchor_shadow.shadow_uploadstatus NOT NULL, "
                "created_at timestamptz NOT NULL)"
            )
        )
        # Every index property and built-in type matches. Only the enum OID differs.
        await conn.execute(
            text(
                "CREATE INDEX ix_upload_requests_user_created_id ON "
                "anchor_shadow.upload_requests (user_id, created_at, id)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_upload_requests_status_created_id ON "
                "anchor_shadow.upload_requests (status, created_at, id)"
            )
        )
        await conn.execute(
            text(f'ALTER DATABASE "{quoted_database}" SET search_path TO anchor_shadow, public')
        )

    try:
        _with_migration_connection(expected_database, prepare)
        if execution == "online":
            command.upgrade(cfg, CURRENT_HEAD_REVISION)
        else:
            migration_0010 = (
                ScriptDirectory.from_config(cfg).get_revision("0010_upload_created_index").module
            )
            migration_0011 = (
                ScriptDirectory.from_config(cfg).get_revision("0011_upload_index_ownership").module
            )

            async def execute_offline(conn: AsyncConnection) -> None:
                await conn.execute(text("SET search_path TO anchor_shadow, public"))
                await conn.execute(text(migration_0010._offline_upgrade_sql()))
                await conn.execute(text(migration_0011._offline_sql(backfill=True)))

            _with_migration_connection(expected_database, execute_offline)

        async def check(conn: AsyncConnection) -> None:
            public_comment = (
                await conn.execute(
                    text(
                        "SELECT obj_description('public.ix_upload_requests_created_id'::regclass, "
                        "'pg_class')"
                    )
                )
            ).scalar_one()
            shadow_rows = (
                await conn.execute(
                    text(
                        "SELECT i.relname,obj_description(i.oid,'pg_class') FROM pg_class i "
                        "JOIN pg_namespace n ON n.oid=i.relnamespace "
                        "WHERE n.nspname='anchor_shadow' ORDER BY i.relname"
                    )
                )
            ).all()
            assert public_comment == INDEX_OWNERSHIP_MARKER
            assert shadow_rows == [
                ("ix_upload_requests_status_created_id", None),
                ("ix_upload_requests_user_created_id", None),
                ("upload_requests", None),
            ]

        _with_migration_connection(expected_database, check)
    finally:

        async def cleanup(conn: AsyncConnection) -> None:
            await conn.execute(text(f'ALTER DATABASE "{quoted_database}" RESET search_path'))
            await conn.execute(text("SET search_path TO public"))
            await conn.execute(text("DROP SCHEMA IF EXISTS anchor_shadow CASCADE"))

        _with_migration_connection(expected_database, cleanup)


def test_0010_downgrade_accepts_user_schema_starting_with_pg(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0009_db_integrity")
    quoted_database = expected_database.replace('"', '""')

    async def prepare(conn: AsyncConnection) -> None:
        await conn.execute(text("CREATE SCHEMA pgapp"))
        await conn.execute(text("ALTER TABLE public.upload_requests SET SCHEMA pgapp"))
        await conn.execute(
            text(f'ALTER DATABASE "{quoted_database}" SET search_path TO pgapp, public')
        )

    try:
        _with_migration_connection(expected_database, prepare)
        command.upgrade(cfg, CURRENT_HEAD_REVISION)

        async def check_upgrade(conn: AsyncConnection) -> None:
            assert await _revision(conn) == CURRENT_HEAD_REVISION
            assert (
                await conn.execute(
                    text("SELECT to_regclass('pgapp.ix_upload_requests_created_id')")
                )
            ).scalar_one() is not None

        _with_migration_connection(expected_database, check_upgrade)
        command.downgrade(cfg, "0009_db_integrity")

        async def check_downgrade(conn: AsyncConnection) -> None:
            assert await _revision(conn) == "0009_db_integrity"
            assert (
                await conn.execute(
                    text("SELECT to_regclass('pgapp.ix_upload_requests_created_id')")
                )
            ).scalar_one() is None

        _with_migration_connection(expected_database, check_downgrade)
    finally:

        async def cleanup(conn: AsyncConnection) -> None:
            await conn.execute(text(f'ALTER DATABASE "{quoted_database}" RESET search_path'))
            await conn.execute(text("SET search_path TO public"))
            if (
                await conn.execute(text("SELECT to_regclass('pgapp.upload_requests')"))
            ).scalar_one():
                await conn.execute(text("ALTER TABLE pgapp.upload_requests SET SCHEMA public"))
            await conn.execute(text("DROP SCHEMA IF EXISTS pgapp CASCADE"))

        _with_migration_connection(expected_database, cleanup)


@pytest.mark.parametrize("replacement", [False, True], ids=["foreign-schema", "same-table"])
def test_0010_downgrade_never_removes_unowned_compatible_index(migration_db, replacement: bool):
    cfg, expected_database = migration_db
    command.upgrade(cfg, CURRENT_HEAD_REVISION)

    async def prepare(conn: AsyncConnection) -> None:
        await conn.execute(text("DROP INDEX public.ix_upload_requests_created_id"))
        if replacement:
            await conn.execute(
                text(
                    "CREATE INDEX ix_upload_requests_created_id "
                    "ON public.upload_requests (created_at, id)"
                )
            )
        else:
            await conn.execute(text("CREATE SCHEMA foreign_owner"))
            await conn.execute(
                text(
                    "CREATE TABLE foreign_owner.upload_requests "
                    "(id bigint NOT NULL, created_at timestamptz NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX ix_upload_requests_created_id "
                    "ON foreign_owner.upload_requests (created_at, id)"
                )
            )

    _with_migration_connection(expected_database, prepare)
    try:
        with pytest.raises(RuntimeError, match="managed index not found"):
            command.downgrade(cfg, "0009_db_integrity")

        async def check(conn: AsyncConnection) -> None:
            assert await _revision(conn) == CURRENT_HEAD_REVISION
            schema = "public" if replacement else "foreign_owner"
            assert (
                await conn.execute(
                    text(f"SELECT to_regclass('{schema}.ix_upload_requests_created_id')")
                )
            ).scalar_one() is not None

        _with_migration_connection(expected_database, check)
    finally:

        async def cleanup(conn: AsyncConnection) -> None:
            await conn.execute(text("DROP SCHEMA IF EXISTS foreign_owner CASCADE"))
            if replacement:
                await conn.execute(text("DROP INDEX public.ix_upload_requests_created_id"))
            await _restore_managed_upload_index(conn)

        _with_migration_connection(expected_database, cleanup)


def test_0010_upgrade_refuses_foreign_index_comment(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0009_db_integrity")

    async def prepare(conn: AsyncConnection) -> None:
        await conn.execute(
            text(
                "CREATE INDEX ix_upload_requests_created_id "
                "ON public.upload_requests (created_at, id)"
            )
        )
        await conn.execute(
            text("COMMENT ON INDEX public.ix_upload_requests_created_id IS 'foreign-owner'")
        )

    _with_migration_connection(expected_database, prepare)
    with pytest.raises(RuntimeError, match="ownership conflict"):
        command.upgrade(cfg, CURRENT_HEAD_REVISION)

    async def check(conn: AsyncConnection) -> None:
        assert await _revision(conn) == "0009_db_integrity"
        assert (
            await conn.execute(
                text(
                    "SELECT obj_description('ix_upload_requests_created_id'::regclass, 'pg_class')"
                )
            )
        ).scalar_one() == "foreign-owner"
        await conn.execute(text("DROP INDEX public.ix_upload_requests_created_id"))

    _with_migration_connection(expected_database, check)


def test_0011_fresh_upgrade_has_owned_index_with_full_signature(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "head")

    async def check(conn: AsyncConnection) -> None:
        assert await _revision(conn) == CURRENT_HEAD_REVISION
        row = (
            await conn.execute(
                text("""
                    SELECT t.relkind::text, ins.nspname, tns.nspname, t.relname,
                           array_agg(a.attname ORDER BY k.ordinality)
                             FILTER (WHERE k.ordinality <= x.indnkeyatts),
                           array_agg(o.option ORDER BY k.ordinality)
                             FILTER (WHERE k.ordinality <= x.indnkeyatts),
                           array_agg(ic.opclass_oid ORDER BY k.ordinality)
                             FILTER (WHERE k.ordinality <= x.indnkeyatts),
                           array_agg(default_opc.oid ORDER BY k.ordinality)
                             FILTER (WHERE k.ordinality <= x.indnkeyatts),
                           x.indnkeyatts, x.indnatts, am.amname, x.indisunique,
                           x.indpred IS NOT NULL, x.indexprs IS NOT NULL,
                           x.indisexclusion,
                           x.indisvalid, x.indisready,
                           obj_description(i.oid, 'pg_class')
                    FROM pg_class i
                    JOIN pg_namespace ins ON ins.oid = i.relnamespace
                    JOIN pg_index x ON x.indexrelid = i.oid
                    JOIN pg_class t ON t.oid = x.indrelid
                    JOIN pg_namespace tns ON tns.oid = t.relnamespace
                    JOIN pg_am am ON am.oid = i.relam
                    LEFT JOIN LATERAL unnest(x.indkey) WITH ORDINALITY
                      AS k(attnum, ordinality) ON TRUE
                    LEFT JOIN LATERAL unnest(x.indoption) WITH ORDINALITY
                      AS o(option, ordinality) ON o.ordinality = k.ordinality
                    LEFT JOIN pg_attribute a
                      ON a.attrelid = x.indrelid AND a.attnum = k.attnum
                    LEFT JOIN LATERAL unnest(x.indclass) WITH ORDINALITY
                      AS ic(opclass_oid, ordinality) ON ic.ordinality = k.ordinality
                    LEFT JOIN pg_opclass default_opc ON default_opc.opcmethod = i.relam
                      AND default_opc.opcintype = a.atttypid AND default_opc.opcdefault
                    WHERE i.oid = 'public.ix_upload_requests_created_id'::regclass
                    GROUP BY i.oid, ins.nspname, tns.nspname, t.relkind, t.relname,
                             x.indnkeyatts, x.indnatts, am.amname, x.indisunique,
                             x.indpred, x.indexprs, x.indisexclusion, x.indisvalid, x.indisready
                """)
            )
        ).one()
        assert row == (
            "r",
            "public",
            "public",
            "upload_requests",
            ["created_at", "id"],
            [0, 0],
            row[6],
            row[6],
            2,
            2,
            "btree",
            False,
            False,
            False,
            False,
            True,
            True,
            INDEX_OWNERSHIP_MARKER,
        )

    _with_migration_connection(expected_database, check)
    command.downgrade(cfg, "base")


def test_0011_backfills_legacy_0010_and_preserves_index_oid(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0010_upload_created_index")

    async def make_legacy(conn: AsyncConnection) -> int:
        oid = (
            await conn.execute(text("SELECT 'public.ix_upload_requests_created_id'::regclass::oid"))
        ).scalar_one()
        await conn.execute(text("COMMENT ON INDEX public.ix_upload_requests_created_id IS NULL"))
        assert await _revision(conn) == "0010_upload_created_index"
        return oid

    old_oid = _with_migration_connection(expected_database, make_legacy)
    command.upgrade(cfg, "head")

    async def check_backfill(conn: AsyncConnection) -> None:
        assert await _revision(conn) == CURRENT_HEAD_REVISION
        row = (
            await conn.execute(
                text(
                    "SELECT i.oid, obj_description(i.oid, 'pg_class'), x.indoption::smallint[] "
                    "FROM pg_class i JOIN pg_index x ON x.indexrelid=i.oid "
                    "WHERE i.oid='public.ix_upload_requests_created_id'::regclass"
                )
            )
        ).one()
        assert row[0] == old_oid
        assert row[1] == INDEX_OWNERSHIP_MARKER
        assert tuple(row[2]) == (0, 0)

    _with_migration_connection(expected_database, check_backfill)
    command.downgrade(cfg, "0009_db_integrity")


def test_0011_does_not_mark_foreign_only_candidate(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0010_upload_created_index")

    async def prepare(conn: AsyncConnection) -> None:
        await conn.execute(text("DROP INDEX public.ix_upload_requests_created_id"))
        await conn.execute(text("CREATE SCHEMA foreign_owner"))
        await conn.execute(
            text(
                "CREATE TABLE foreign_owner.upload_requests "
                "(id bigint NOT NULL, created_at timestamptz NOT NULL)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_upload_requests_created_id "
                "ON foreign_owner.upload_requests (created_at, id)"
            )
        )

    _with_migration_connection(expected_database, prepare)
    try:
        with pytest.raises(RuntimeError, match="managed index not found"):
            command.upgrade(cfg, "head")

        async def check(conn: AsyncConnection) -> None:
            assert await _revision(conn) == "0010_upload_created_index"
            assert (
                await conn.execute(
                    text(
                        "SELECT obj_description("
                        "'foreign_owner.ix_upload_requests_created_id'::regclass, 'pg_class')"
                    )
                )
            ).scalar_one_or_none() is None

        _with_migration_connection(expected_database, check)
    finally:

        async def cleanup(conn: AsyncConnection) -> None:
            await conn.execute(text("DROP SCHEMA foreign_owner CASCADE"))
            await _restore_managed_upload_index(conn)

        _with_migration_connection(expected_database, cleanup)


def test_0011_rejects_foreign_marked_index_before_adopting_target(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0010_upload_created_index")

    async def prepare(conn: AsyncConnection) -> tuple[int, int]:
        target_oid = (
            await conn.execute(text("SELECT 'public.ix_upload_requests_created_id'::regclass::oid"))
        ).scalar_one()
        await conn.execute(text("COMMENT ON INDEX public.ix_upload_requests_created_id IS NULL"))
        await conn.execute(text("CREATE SCHEMA foreign_owner"))
        await conn.execute(
            text(
                "CREATE TABLE foreign_owner.upload_requests "
                "(id integer NOT NULL, created_at timestamptz NOT NULL)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_upload_requests_created_id "
                "ON foreign_owner.upload_requests (created_at, id)"
            )
        )
        await conn.execute(
            text(
                "COMMENT ON INDEX foreign_owner.ix_upload_requests_created_id IS "
                "'yd_upd_approver:alembic:0010_upload_created_index'"
            )
        )
        foreign_oid = (
            await conn.execute(
                text("SELECT 'foreign_owner.ix_upload_requests_created_id'::regclass::oid")
            )
        ).scalar_one()
        await conn.execute(text("SET search_path TO foreign_owner, public"))
        return target_oid, foreign_oid

    target_oid, foreign_oid = _with_migration_connection(expected_database, prepare)
    try:
        with pytest.raises(RuntimeError, match="incompatible index signature"):
            command.upgrade(cfg, "head")

        async def check(conn: AsyncConnection) -> None:
            assert await _revision(conn) == "0010_upload_created_index"
            rows = (
                await conn.execute(
                    text(
                        "SELECT i.oid, obj_description(i.oid,'pg_class') FROM pg_class i "
                        "WHERE i.oid IN (:target_oid,:foreign_oid) ORDER BY i.oid"
                    ),
                    {"target_oid": target_oid, "foreign_oid": foreign_oid},
                )
            ).all()
            assert dict(rows) == {target_oid: None, foreign_oid: INDEX_OWNERSHIP_MARKER}

        _with_migration_connection(expected_database, check)
    finally:

        async def cleanup(conn: AsyncConnection) -> None:
            await conn.execute(text("DROP SCHEMA IF EXISTS foreign_owner CASCADE"))
            await _restore_managed_upload_index(conn)

        _with_migration_connection(expected_database, cleanup)


def test_0011_refuses_foreign_legacy_comment(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0010_upload_created_index")

    async def prepare(conn: AsyncConnection) -> int:
        oid = (
            await conn.execute(text("SELECT 'public.ix_upload_requests_created_id'::regclass::oid"))
        ).scalar_one()
        await conn.execute(
            text("COMMENT ON INDEX public.ix_upload_requests_created_id IS 'foreign-owner'")
        )
        return oid

    try:
        old_oid = _with_migration_connection(expected_database, prepare)
        with pytest.raises(RuntimeError, match="ownership conflict"):
            command.upgrade(cfg, "head")

        async def check(conn: AsyncConnection) -> None:
            assert await _revision(conn) == "0010_upload_created_index"
            row = (
                await conn.execute(
                    text(
                        "SELECT i.oid, obj_description(i.oid, 'pg_class') "
                        "FROM pg_class i "
                        "WHERE i.oid = 'public.ix_upload_requests_created_id'::regclass"
                    )
                )
            ).one()
            assert row == (old_oid, "foreign-owner")

        _with_migration_connection(expected_database, check)
    finally:
        _with_migration_connection(expected_database, _restore_managed_upload_index)


def test_0011_backfills_after_empty_comment_is_removed_by_postgresql(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "0010_upload_created_index")

    async def remove_comment(conn: AsyncConnection) -> int:
        oid = (
            await conn.execute(text("SELECT 'public.ix_upload_requests_created_id'::regclass::oid"))
        ).scalar_one()
        await conn.execute(text("COMMENT ON INDEX public.ix_upload_requests_created_id IS ''"))
        comment = (
            await conn.execute(
                text("SELECT obj_description(:index_oid, 'pg_class')"), {"index_oid": oid}
            )
        ).scalar_one_or_none()
        assert comment is None
        return oid

    old_oid = _with_migration_connection(expected_database, remove_comment)
    command.upgrade(cfg, "head")

    async def check(conn: AsyncConnection) -> None:
        assert await _revision(conn) == CURRENT_HEAD_REVISION
        row = (
            await conn.execute(
                text(
                    "SELECT i.oid, obj_description(i.oid, 'pg_class') "
                    "FROM pg_class i "
                    "WHERE i.oid = 'public.ix_upload_requests_created_id'::regclass"
                )
            )
        ).one()
        assert row == (old_oid, INDEX_OWNERSHIP_MARKER)

    _with_migration_connection(expected_database, check)


def test_0011_downgrade_preserves_marker_for_strict_0010_downgrade(migration_db):
    cfg, expected_database = migration_db
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0010_upload_created_index")

    async def at_0010(conn: AsyncConnection) -> None:
        assert await _revision(conn) == "0010_upload_created_index"
        assert (
            await conn.execute(
                text(
                    "SELECT obj_description("
                    "'public.ix_upload_requests_created_id'::regclass, 'pg_class')"
                )
            )
        ).scalar_one() == INDEX_OWNERSHIP_MARKER

    _with_migration_connection(expected_database, at_0010)
    command.downgrade(cfg, "0009_db_integrity")


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
