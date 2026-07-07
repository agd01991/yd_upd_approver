from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import get_user_by_tg, list_user_requests
from app.services.yandex_disk import YandexDiskClient

router = Router()


@router.message(Command("profile"))
async def profile(message: Message, session: AsyncSession) -> None:
    user = await get_user_by_tg(session, message.from_user.id)
    if not user:
        await message.answer("Сначала выполните /start")
        return
    await message.answer(
        f"Статус: {user.status.value}\nПапка: {user.root_folder or 'не назначена'}"
    )


@router.message(Command("status"))
async def status(message: Message, session: AsyncSession) -> None:
    user = await get_user_by_tg(session, message.from_user.id)
    if not user:
        await message.answer("Нет заявок")
        return
    requests = await list_user_requests(session, user.id)
    await message.answer(
        "\n".join(f"{r.request_code}: {r.safe_filename} — {r.status.value}" for r in requests)
        or "Нет заявок"
    )


@router.message(Command("myfiles"))
async def myfiles(
    message: Message, session: AsyncSession, yandex_client: YandexDiskClient | None = None
) -> None:
    user = await get_user_by_tg(session, message.from_user.id)
    if not user or not user.root_folder:
        await message.answer("Папка ещё не назначена")
        return
    if not yandex_client:
        await message.answer(f"Ваша папка: {user.root_folder}")
        return
    files = await yandex_client.list_files(user.root_folder)
    await message.answer("\n".join(item.get("name", "?") for item in files) or "Папка пуста")
