import asyncio
import logging

from aiogram import Bot, Dispatcher

from app.bot.middlewares import DbSessionMiddleware, SettingsMiddleware
from app.bot.router import build_router
from app.config import get_settings
from app.logging_config import configure_logging


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.telegram_bot_token:
        logging.getLogger(__name__).warning(
            "TELEGRAM_BOT_TOKEN is empty; startup check finished without polling"
        )
        return
    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.update.middleware(SettingsMiddleware())
    dispatcher.update.middleware(DbSessionMiddleware())
    dispatcher.include_router(build_router())
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
