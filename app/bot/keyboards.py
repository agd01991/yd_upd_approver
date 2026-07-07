from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import UploadModerationCallback, UserModerationCallback


def user_moderation_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Одобрить",
                    callback_data=UserModerationCallback(action="approve", user_id=user_id).pack(),
                ),
                InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=UserModerationCallback(action="reject", user_id=user_id).pack(),
                ),
                InlineKeyboardButton(
                    text="Блок",
                    callback_data=UserModerationCallback(action="block", user_id=user_id).pack(),
                ),
            ]
        ]
    )


def upload_keyboard(request_id: int) -> InlineKeyboardMarkup:
    actions = [
        ("Открыть файл", "open"),
        ("Загрузить", "approve"),
        ("Отклонить", "reject"),
        ("Переименовать", "rename"),
        ("Сменить папку", "folder"),
        ("Содержимое папки", "list"),
        ("Как копию", "copy"),
        ("Перезаписать", "overwrite"),
        ("Повторить", "retry"),
    ]
    rows = [
        [
            InlineKeyboardButton(
                text=t,
                callback_data=UploadModerationCallback(action=a, request_id=request_id).pack(),
            )
        ]
        for t, a in actions
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
