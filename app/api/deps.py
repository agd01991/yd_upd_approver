from collections.abc import AsyncIterator

from aiogram import Bot
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security import TelegramWebAppUser, validate_init_data
from app.config import Settings, get_settings
from app.db.models import User
from app.db.repositories import get_or_create_user
from app.db.session import get_session


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in get_session():
        yield session


def settings_dep() -> Settings:
    return get_settings()


async def telegram_user_dep(
    x_telegram_init_data: str = Header(default=""),
    settings: Settings = Depends(settings_dep),
) -> TelegramWebAppUser:
    return validate_init_data(x_telegram_init_data, settings)


async def current_user_dep(
    tg_user: TelegramWebAppUser = Depends(telegram_user_dep),
    session: AsyncSession = Depends(get_db),
) -> tuple[User, bool]:
    return await get_or_create_user(
        session, tg_user.telegram_id, tg_user.username, tg_user.full_name
    )


async def admin_user_dep(
    tg_user: TelegramWebAppUser = Depends(telegram_user_dep),
    settings: Settings = Depends(settings_dep),
) -> TelegramWebAppUser:
    if tg_user.telegram_id not in settings.telegram_admin_ids:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return tg_user


async def bot_dep(settings: Settings = Depends(settings_dep)) -> AsyncIterator[Bot | None]:
    if not settings.telegram_bot_token:
        yield None
        return
    bot = Bot(settings.telegram_bot_token)
    try:
        yield bot
    finally:
        await bot.session.close()
