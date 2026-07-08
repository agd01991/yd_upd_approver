from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import user_moderation_keyboard
from app.config import Settings
from app.db.models import UserStatus
from app.db.repositories import get_or_create_user
from app.utils.formatting import format_user_card

router = Router()


def webapp_keyboard(settings: Settings) -> InlineKeyboardMarkup | None:
    if not settings.webapp_url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть приложение", web_app=WebAppInfo(url=settings.webapp_url)
                )
            ]
        ]
    )


@router.message(Command("start"))
async def start(message: Message, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    user, created = await get_or_create_user(
        session,
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )
    await session.commit()

    if user.status == UserStatus.active:
        await message.answer(
            "Вы уже одобрены. Можете отправлять файлы.", reply_markup=webapp_keyboard(settings)
        )
        return
    if user.status == UserStatus.blocked:
        await message.answer("Ваш доступ заблокирован. Обратитесь к администратору.")
        return
    if user.status == UserStatus.rejected:
        await message.answer("Ваша заявка на доступ отклонена.")
        return

    if created and user.status == UserStatus.pending:
        for admin_id in settings.telegram_admin_ids:
            await bot.send_message(
                admin_id,
                format_user_card(user),
                reply_markup=user_moderation_keyboard(user.id),
            )
    await message.answer(
        "Вы зарегистрированы. Дождитесь одобрения администратора.",
        reply_markup=webapp_keyboard(settings),
    )


@router.message(Command("app"))
async def app_cmd(message: Message, settings: Settings) -> None:
    if not settings.webapp_url:
        await message.answer("Mini App URL не настроен.")
        return
    await message.answer("Откройте Mini App:", reply_markup=webapp_keyboard(settings))


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer("Команды: /profile, /status, /myfiles. Отправьте файл после одобрения.")
