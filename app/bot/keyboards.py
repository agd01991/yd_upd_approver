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


def upload_keyboard(
    request_id: int | object, status: str | object | None = None
) -> InlineKeyboardMarkup:
    if status is None:
        status = getattr(request_id, "status", None)
    real_id = getattr(request_id, "id", request_id)
    actions = [
        ("Открыть файл", "open"),
        ("Загрузить", "approve"),
        ("Отклонить", "reject"),
        ("Изменить имя", "rename_stem"),
        ("Изменить расширение", "rename_extension"),
        ("Сменить папку этой заявки", "folder"),
        ("Содержимое папки", "list"),
        ("Загрузить как копию", "copy"),
        ("Перезаписать", "overwrite"),
        ("Повторить", "retry"),
    ]
    if getattr(status, "value", status) in {"approved", "uploading", "uploaded", "rejected"}:
        actions = [("Открыть файл", "open"), ("Содержимое папки", "list")]
    elif getattr(status, "value", status) == "failed":
        actions = [
            ("Открыть файл", "open"),
            ("Отклонить", "reject"),
            ("Содержимое папки", "list"),
            ("Загрузить как копию", "copy"),
            ("Перезаписать", "overwrite"),
            ("Повторить", "retry"),
        ]
    rows = [
        [
            InlineKeyboardButton(
                text=t,
                callback_data=UploadModerationCallback(action=a, request_id=real_id).pack(),
            )
        ]
        for t, a in actions
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def folder_selection_keyboard(request_id: int, folders: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=folder[-60:],
                callback_data=UploadModerationCallback(
                    action=f"folder_{index}", request_id=request_id
                ).pack(),
            )
        ]
        for index, folder in enumerate(folders)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reject_reason_keyboard(request_id: int) -> InlineKeyboardMarkup:
    reasons = [
        ("Дубликат", "reject_duplicate"),
        ("Неверный файл", "reject_wrong_file"),
        ("Плохое качество", "reject_bad_quality"),
        ("Не та папка", "reject_wrong_folder"),
        ("Другое", "reject_other"),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=UploadModerationCallback(
                        action=action, request_id=request_id
                    ).pack(),
                )
            ]
            for text, action in reasons
        ]
    )
