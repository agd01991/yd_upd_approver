from aiogram.types import CallbackQuery

from app.config import Settings


def is_admin(telegram_id: int, settings: Settings) -> bool:
    return int(telegram_id) in set(settings.telegram_admin_ids)


def ensure_admin_callback(callback: CallbackQuery, settings: Settings) -> bool:
    return bool(callback.from_user and is_admin(callback.from_user.id, settings))
