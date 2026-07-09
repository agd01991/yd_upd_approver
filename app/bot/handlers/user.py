from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.repositories import get_user_by_tg, list_user_requests
from app.services.yandex_disk import YandexDiskClient, YandexDiskError
from app.utils.formatting import format_folder_items

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
async def myfiles(message: Message, session: AsyncSession, settings: Settings) -> None:
    user = await get_user_by_tg(session, message.from_user.id)
    if not user or not user.root_folder:
        await message.answer("Папка ещё не назначена")
        return
    client = YandexDiskClient(settings.yandex_disk_token)
    try:
        try:
            files = await client.list_files(user.root_folder)
        except FileNotFoundError:
            await message.answer("Папка ещё не создана")
            return
        except YandexDiskError as exc:
            await message.answer(f"Не удалось получить список файлов: {exc}")
            return
    finally:
        await client.close()
    await message.answer(format_folder_items(user.root_folder, files))


@router.message(Command("renamefolder"))
async def renamefolder(message: Message, session: AsyncSession) -> None:
    user = await get_user_by_tg(session, message.from_user.id)
    if not user or user.status.value != "active":
        await message.answer("Заявка на переименование доступна только одобренным пользователям.")
        return
    await message.answer(
        "Создать заявку на переименование можно в Mini App: укажите номер договора, дату, ФИО и новое имя папки. "
        "Администратор выберет текущую или предыдущую папку и выполнит переименование."
    )
