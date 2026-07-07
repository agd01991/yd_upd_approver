from pathlib import Path

from aiogram import Bot


async def download_file(bot: Bot, file_id: str, destination: Path) -> Path:
    file = await bot.get_file(file_id)
    await bot.download_file(file.file_path, destination=destination)
    return destination
