import asyncio
import hashlib
import os
import threading

import pytest

from app.services.storage import TempStorage


async def _chunks(*items: bytes):
    for item in items:
        yield item


@pytest.mark.anyio
async def test_save_async_chunks_waits_for_cancelled_replace_and_cleans_destination(
    monkeypatch, tmp_path
) -> None:
    storage = TempStorage(tmp_path)
    real_replace = os.replace
    replace_started = threading.Event()
    allow_replace = threading.Event()
    replace_finished = threading.Event()

    def blocked_replace(src, dst):  # noqa: ANN001
        replace_started.set()
        assert allow_replace.wait(timeout=2)
        real_replace(src, dst)
        replace_finished.set()

    monkeypatch.setattr(os, "replace", blocked_replace)
    upload = asyncio.create_task(storage.save_async_chunks("REQ", "file.bin", _chunks(b"abc")))

    assert await asyncio.to_thread(replace_started.wait, 2)
    upload.cancel()
    await asyncio.sleep(0)
    assert not upload.done()
    allow_replace.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(upload, timeout=2)

    assert replace_finished.is_set()
    assert not (tmp_path / "REQ" / "file.bin").exists()
    assert not list(tmp_path.glob("**/*.part"))
    assert not (tmp_path / "REQ").exists()


@pytest.mark.anyio
async def test_save_async_chunks_success_returns_path_size_and_sha256(tmp_path) -> None:
    storage = TempStorage(tmp_path)

    stored = await storage.save_async_chunks("REQ", "file.bin", _chunks(b"abc", b"def"))

    assert stored.path == tmp_path.resolve() / "REQ" / "file.bin"
    assert stored.size_bytes == 6
    assert stored.sha256 == hashlib.sha256(b"abcdef").hexdigest()
    assert stored.path.read_bytes() == b"abcdef"
    assert not list(tmp_path.glob("**/*.part"))


@pytest.mark.anyio
async def test_save_async_chunks_replace_error_removes_part(monkeypatch, tmp_path) -> None:
    storage = TempStorage(tmp_path)

    def fail_replace(src, dst):  # noqa: ANN001
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        await storage.save_async_chunks("REQ", "file.bin", _chunks(b"abc"))

    assert not (tmp_path / "REQ" / "file.bin").exists()
    assert not list(tmp_path.glob("**/*.part"))


@pytest.mark.anyio
async def test_save_async_chunks_cleanup_error_preserves_cancelled_error(
    monkeypatch, tmp_path
) -> None:
    storage = TempStorage(tmp_path)
    real_replace = os.replace
    replace_started = threading.Event()
    allow_replace = threading.Event()

    def blocked_replace(src, dst):  # noqa: ANN001
        replace_started.set()
        assert allow_replace.wait(timeout=2)
        real_replace(src, dst)

    real_run_blocking = storage._run_blocking_file_op

    async def fail_cleanup_op(operation, /, *args, cleanup_context):  # noqa: ANN001
        if cleanup_context == "cleanup":
            raise RuntimeError("cleanup failed")
        return await real_run_blocking(operation, *args, cleanup_context=cleanup_context)

    monkeypatch.setattr(os, "replace", blocked_replace)
    monkeypatch.setattr(storage, "_run_blocking_file_op", fail_cleanup_op)
    upload = asyncio.create_task(storage.save_async_chunks("REQ", "file.bin", _chunks(b"abc")))

    assert await asyncio.to_thread(replace_started.wait, 2)
    upload.cancel()
    allow_replace.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(upload, timeout=2)
