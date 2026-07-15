import asyncio
import hashlib
import os
from uuid import uuid4

from aiogram import Bot

from app.services.storage import CHUNK_SIZE, StoredFile, TempStorage


class _BoundedStagingWriter:
    def __init__(
        self, storage: TempStorage, request_code: str, filename: str, max_bytes: int
    ) -> None:
        self.destination = storage.path_for(request_code, filename)
        self.part = self.destination.with_name(f".{self.destination.name}.{uuid4().hex}.part")
        self._storage = storage
        self._max_bytes = max_bytes
        self._digest = hashlib.sha256()
        self._size = 0
        self._file = self.part.open("xb")
        self.max_chunk_size = 0

    @property
    def size_bytes(self) -> int:
        return self._size

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()

    def write(self, chunk: bytes) -> int:
        if not chunk:
            return 0
        self.max_chunk_size = max(self.max_chunk_size, len(chunk))
        next_size = self._size + len(chunk)
        if next_size > self._max_bytes:
            raise ValueError("file exceeds max size")
        self._digest.update(chunk)
        written = self._file.write(chunk)
        self._size = next_size
        return written

    def flush(self) -> None:
        self._file.flush()

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._file.seek(offset, whence)

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def commit(self) -> StoredFile:
        self.close()
        os.replace(self.part, self.destination)
        return StoredFile(self.destination, self.size_bytes, self.sha256)

    def cleanup(self) -> None:
        self.close()
        self.part.unlink(missing_ok=True)
        self._storage._cleanup_empty_request_dir(self.destination)  # noqa: SLF001


async def download_file(
    bot: Bot,
    file_id: str,
    storage: TempStorage,
    request_code: str,
    filename: str,
    *,
    max_bytes: int,
) -> StoredFile:
    tg_file = await bot.get_file(file_id)
    writer = _BoundedStagingWriter(storage, request_code, filename, max_bytes)
    try:
        await bot.download_file(
            tg_file.file_path,
            destination=writer,
            chunk_size=CHUNK_SIZE,
            seek=False,
        )
        return await asyncio.to_thread(writer.commit)
    except BaseException:
        await asyncio.to_thread(writer.cleanup)
        raise
