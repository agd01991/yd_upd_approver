import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import AuditLog, UploadRequest, UploadSource, UploadStatus, User, UserStatus
from app.services.upload_queue import (
    UploadQueueError,
    change_upload_folder,
    enqueue_upload_request,
    reject_upload_request,
)

pytestmark = pytest.mark.anyio


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="PostgreSQL DATABASE_URL is required for row-lock test"
)
async def test_concurrent_enqueue_and_reject_row_lock(tmp_path: Path) -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    local_file = tmp_path / "upload.txt"
    local_file.write_text("hello")
    code = f"REQ-{datetime.now(UTC).timestamp():.6f}"
    async with Session() as session:
        user = User(telegram_id=900000001, status=UserStatus.active, root_folder="disk:/root/u")
        session.add(user)
        await session.flush()
        request = UploadRequest(
            request_code=code,
            user_id=user.id,
            source=UploadSource.mini_app,
            telegram_file_id=None,
            telegram_file_unique_id=None,
            original_filename="upload.txt",
            safe_filename="upload.txt",
            mime_type="text/plain",
            size_bytes=5,
            sha256="a" * 64,
            caption=None,
            local_path=str(local_file),
            target_folder="disk:/root/u",
            target_path="disk:/root/u/upload.txt",
            status=UploadStatus.pending_approval,
        )
        session.add(request)
        await session.commit()
        request_id = request.id

    async def enqueue_once():
        async with Session() as session:
            try:
                await enqueue_upload_request(session, request_id, "approve", 100)
                return "enqueue"
            except UploadQueueError:
                await session.rollback()
                return "enqueue_failed"

    async def reject_once():
        async with Session() as session:
            try:
                await reject_upload_request(session, request_id, 101, "reject")
                return "reject"
            except UploadQueueError:
                await session.rollback()
                return "reject_failed"

    results = set(await asyncio.gather(enqueue_once(), reject_once()))
    assert results in ({"enqueue", "reject_failed"}, {"reject", "enqueue_failed"})

    async with Session() as session:
        request = await session.get(UploadRequest, request_id)
        assert request is not None
        if "reject" in results:
            assert request.status == UploadStatus.rejected
            assert request.worker_token is None
        else:
            assert request.status == UploadStatus.approved
        audit_actions = (
            await session.scalars(
                select(AuditLog.action)
                .where(AuditLog.request_id == request_id)
                .order_by(AuditLog.id)
            )
        ).all()
        assert not {"upload_approve", "upload_reject"}.issubset(set(audit_actions))
    await engine.dispose()


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="PostgreSQL DATABASE_URL is required for stale identity map regression test",
)
async def test_change_upload_folder_refreshes_locked_request_identity_map(tmp_path: Path) -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    local_file = tmp_path / "stale.txt"
    local_file.write_text("hello")
    code = f"REQ-STAL-{datetime.now(UTC).timestamp():.6f}"
    async with Session() as setup_session:
        user = User(
            telegram_id=900000002,
            status=UserStatus.active,
            root_folder="disk:/root/u/",
            allowed_folders=["disk:/root/u/", "disk:/root/u/docs/"],
        )
        setup_session.add(user)
        await setup_session.flush()
        request = UploadRequest(
            request_code=code,
            user_id=user.id,
            source=UploadSource.mini_app,
            telegram_file_id=None,
            telegram_file_unique_id=None,
            original_filename="stale.txt",
            safe_filename="stale.txt",
            mime_type="text/plain",
            size_bytes=5,
            sha256="b" * 64,
            caption=None,
            local_path=str(local_file),
            target_folder="disk:/root/u/",
            target_path="disk:/root/u/stale.txt",
            status=UploadStatus.pending_approval,
        )
        setup_session.add(request)
        await setup_session.commit()
        request_id = request.id

    async with Session() as session_1:
        stale_request = await session_1.get(UploadRequest, request_id)
        assert stale_request is not None
        assert stale_request.status == UploadStatus.pending_approval

        async with Session() as session_2:
            fresh_request = await session_2.get(UploadRequest, request_id)
            assert fresh_request is not None
            fresh_request.status = UploadStatus.approved
            await session_2.commit()

        with pytest.raises(UploadQueueError):
            await change_upload_folder(session_1, request_id, "disk:/root/u/docs/", 100)
        await session_1.rollback()

    async with Session() as verify_session:
        request = await verify_session.get(UploadRequest, request_id)
        assert request is not None
        assert request.status == UploadStatus.approved
        assert request.target_folder == "disk:/root/u/"
        assert request.target_path == "disk:/root/u/stale.txt"
        audit_actions = (
            await verify_session.scalars(
                select(AuditLog.action).where(AuditLog.request_id == request_id)
            )
        ).all()
        assert "upload_folder_change" not in audit_actions
    await engine.dispose()
