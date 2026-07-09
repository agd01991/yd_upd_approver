from typing import Any

from app.db.models import UploadRequest, User


def _enum_value(value: object) -> str:
    return getattr(value, "value", str(value))


USER_STATUS_LABELS = {
    "pending": "ожидает одобрения",
    "active": "активен",
    "rejected": "отклонён",
    "blocked": "заблокирован",
}

UPLOAD_STATUS_LABELS = {
    "stored": "сохранён временно",
    "new": "новый",
    "pending_approval": "ожидает проверки",
    "approved": "одобрено",
    "uploading": "загружается",
    "uploaded": "загружено",
    "failed": "ошибка загрузки",
    "rejected": "отклонён",
    "cancelled": "отменено",
    "deleted_temp": "временный файл удалён",
}

AUDIT_ACTION_LABELS = {
    "upload_filename_stem_change": "изменение имени файла",
    "upload_filename_extension_change": "изменение расширения файла",
    "upload_patch": "изменение заявки",
    "upload_folder_change": "изменение папки",
    "upload_approve": "загрузка одобрена",
    "upload_reject": "заявка отклонена",
    "upload_copy_path": "загрузка как копия",
}


def format_user_status(status: object) -> str:
    value = _enum_value(status)
    return USER_STATUS_LABELS.get(value, value)


def format_upload_status(status: object) -> str:
    value = _enum_value(status)
    return UPLOAD_STATUS_LABELS.get(value, value)


def format_audit_action(action: str) -> str:
    return AUDIT_ACTION_LABELS.get(action, action)


def short_sha256(value: str) -> str:
    return value[:12]


def human_size(size: int | None) -> str:
    size = size or 0
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def format_user_card(user: User) -> str:
    username = f"@{user.username}" if user.username else "—"
    return (
        "Новый пользователь\n"
        f"Имя: {user.full_name or '—'}\n"
        f"Username Telegram: {username}\n"
        f"ID Telegram: {user.telegram_id}\n"
        f"Статус: {format_user_status(user.status)}"
    )


def format_upload_card(upload: UploadRequest, user: User) -> str:
    username = f"@{user.username}" if user.username else "—"
    return (
        "Заявка на загрузку файла\n"
        f"Номер: {upload.request_code}\n"
        f"Пользователь: {user.full_name or '—'} / {username} / {user.telegram_id}\n"
        f"Файл: {upload.safe_filename}\n"
        f"Размер: {human_size(upload.size_bytes)}\n"
        f"Тип файла: {upload.mime_type or '—'}\n"
        f"SHA-256: {short_sha256(upload.sha256)}\n"
        f"Комментарий: {upload.caption or '—'}\n"
        f"Целевая папка: {upload.target_folder}\n"
        f"Путь на Яндекс.Диске: {upload.target_path}\n"
        f"Статус: {format_upload_status(upload.status)}"
    )


def format_folder_items(folder: str, items: list[dict[str, Any]]) -> str:
    if not items:
        return f"Содержимое папки:\n{folder}\n\nПапка пуста"
    lines = ["Содержимое папки:", folder, ""]
    for index, item in enumerate(items, start=1):
        name = item.get("name", "?")
        size = human_size(item.get("size") or 0)
        lines.append(f"{index}. {name} — {size}")
    return "\n".join(lines)


def format_upload_result(upload: UploadRequest) -> str:
    if upload.status.value == "uploaded":
        return f"Файл загружен: {upload.request_code}\n{upload.target_path}"
    if upload.status.value == "failed":
        return (
            f"Ошибка загрузки {upload.request_code}: {upload.error_message or 'неизвестная ошибка'}"
        )
    if upload.status.value == "rejected":
        return f"Файл отклонён: {upload.request_code}. Причина: {upload.reject_reason or '—'}"
    return f"Заявка {upload.request_code}: {format_upload_status(upload.status)}"
