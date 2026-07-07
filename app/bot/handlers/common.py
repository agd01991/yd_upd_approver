from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import get_or_create_user

router = Router()


@router.message(Command("start"))
async def start(message: Message, session: AsyncSession | None = None) -> None:
    if session:
        await get_or_create_user(
            session, message.from_user.id, message.from_user.username, message.from_user.full_name
        )
        await session.commit()
    await message.answer("Вы зарегистрированы. Дождитесь одобрения администратора.")


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer("Команды: /profile, /status, /myfiles. Отправьте файл после одобрения.")
