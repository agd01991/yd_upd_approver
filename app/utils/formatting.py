from typing import Any

from app.db.models import UploadRequest, User


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
        f"Username: {username}\n"
        f"Telegram ID: {user.telegram_id}\n"
        f"Статус: {user.status.value}"
    )


def format_upload_card(upload: UploadRequest, user: User) -> str:
    username = f"@{user.username}" if user.username else "—"
    return (
        "Заявка на загрузку файла\n"
        f"Номер: {upload.request_code}\n"
        f"Пользователь: {user.full_name or '—'} / {username} / {user.telegram_id}\n"
        f"Файл: {upload.safe_filename}\n"
        f"Размер: {human_size(upload.size_bytes)}\n"
        f"MIME: {upload.mime_type or '—'}\n"
        f"SHA256: {short_sha256(upload.sha256)}\n"
        f"Комментарий: {upload.caption or '—'}\n"
        f"Целевая папка: {upload.target_folder}\n"
        f"Target path: {upload.target_path}\n"
        f"Статус: {upload.status.value}"
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
    return f"Заявка {upload.request_code}: {upload.status.value}"
