import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from app.services.storage import CHUNK_SIZE, TempStorage
from app.services.telegram_files import download_file


class FakeBot:
    def __init__(self, chunks, *, fail_at=None) -> None:  # noqa: ANN001
        self.chunks = chunks
        self.fail_at = fail_at
        self.calls = []

    async def get_file(self, file_id):  # noqa: ANN001
        return SimpleNamespace(file_path="telegram/path")

    async def download_file(self, file_path, destination=None, **kwargs):  # noqa: ANN001
        self.calls.append((file_path, destination, kwargs))
        assert destination is not None
        for index, chunk in enumerate(self.chunks, start=1):
            if self.fail_at == index:
                raise RuntimeError("network")
            destination.write(chunk)
            destination.flush()
            await asyncio.sleep(0)
        return destination


@pytest.mark.anyio
async def test_telegram_download_streams_chunks_without_bytesio(tmp_path) -> None:
    chunks = [b"a" * 3, b"b" * 5, b"c" * 7]
    bot = FakeBot(chunks)

    stored = await download_file(
        bot, "file-id", TempStorage(tmp_path), "REQ", "file.bin", max_bytes=100
    )

    assert stored.size_bytes == sum(map(len, chunks))
    assert stored.sha256 == hashlib.sha256(b"".join(chunks)).hexdigest()
    assert stored.path.read_bytes() == b"".join(chunks)
    _, destination, kwargs = bot.calls[0]
    assert destination is not None
    assert kwargs["chunk_size"] == CHUNK_SIZE
    assert kwargs["seek"] is False
    assert destination.max_chunk_size == 7
    assert not list(tmp_path.glob("**/*.part"))


@pytest.mark.anyio
async def test_telegram_download_limit_removes_staged_data(tmp_path) -> None:
    bot = FakeBot([b"12345", b"67890"])

    with pytest.raises(ValueError):
        await download_file(bot, "file-id", TempStorage(tmp_path), "REQ", "file.bin", max_bytes=6)

    assert not (tmp_path / "REQ" / "file.bin").exists()
    assert not list(tmp_path.glob("**/*.part"))
    assert not (tmp_path / "REQ").exists()
