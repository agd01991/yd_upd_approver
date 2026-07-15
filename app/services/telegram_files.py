from collections.abc import AsyncIterator

from aiogram import Bot

from app.services.storage import CHUNK_SIZE, StoredFile, TempStorage


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

    async def chunks() -> AsyncIterator[bytes]:
        stream = await bot.download_file(tg_file.file_path)
        while chunk := await __import__("asyncio").to_thread(stream.read, CHUNK_SIZE):
            yield chunk
        close = getattr(stream, "close", None)
        if close:
            close()

    return await storage.save_async_chunks(request_code, filename, chunks(), max_bytes=max_bytes)
