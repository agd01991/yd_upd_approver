from aiogram.filters.callback_data import CallbackData


class UserModerationCallback(CallbackData, prefix="user"):
    action: str
    user_id: int


class UploadModerationCallback(CallbackData, prefix="upload"):
    action: str
    request_id: int
