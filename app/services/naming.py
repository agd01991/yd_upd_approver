import re
from datetime import date
from pathlib import PurePosixPath

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_BAD = re.compile(r"[^A-Za-z0-9А-Яа-яЁё._ -]+")


def sanitize_filename(name: str, default: str = "file") -> str:
    candidate = PurePosixPath(name or default).name
    candidate = _CONTROL.sub("", candidate).replace("/", "_").replace("\\", "_")
    candidate = _BAD.sub("_", candidate).strip(" .")
    if candidate in {"", ".", ".."} or ".." in candidate:
        candidate = default
    return candidate[:255]


def user_folder(root: str, telegram_id: int, full_name: str | None, username: str | None) -> str:
    label = sanitize_filename(username or full_name or "user").replace(" ", "_").lower()
    return f"{root.rstrip('/')}/{telegram_id}_{label}/"


def join_disk_path(folder: str, filename: str) -> str:
    safe = sanitize_filename(filename)
    return f"{folder.rstrip('/')}/{safe}"


def copy_filename(filename: str, request_code: str, today: date | None = None) -> str:
    today = today or date.today()
    path = PurePosixPath(filename)
    stem = path.stem or "file"
    suffix = path.suffix
    return sanitize_filename(f"{stem}__{today.isoformat()}__{request_code}{suffix}")


class FilenameEditError(ValueError):
    """Ошибка безопасного изменения имени файла."""


def _reject_filename_part(value: str, field: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raise FilenameEditError(f"{field} не может быть пустым")
    if _CONTROL.search(raw) or "/" in raw or "\\" in raw or ".." in raw:
        raise FilenameEditError(f"{field} содержит недопустимые символы")
    return raw


def change_filename_stem(current_filename: str, new_stem: str) -> str:
    current = sanitize_filename(current_filename)
    current_path = PurePosixPath(current)
    suffix = current_path.suffix
    stem = _reject_filename_part(new_stem, "Имя файла")
    entered = PurePosixPath(stem)
    if entered.suffix:
        if suffix and entered.suffix.lower() == suffix.lower():
            stem = entered.stem
        else:
            raise FilenameEditError("Расширение меняется отдельным действием")
    safe_stem = sanitize_filename(stem, default="file")
    if not safe_stem or PurePosixPath(safe_stem).suffix:
        raise FilenameEditError("Имя файла содержит недопустимые символы")
    result = sanitize_filename(f"{safe_stem}{suffix}")
    if result != f"{safe_stem}{suffix}" or len(result) > 255:
        raise FilenameEditError("Итоговое имя файла слишком длинное или небезопасное")
    return result


def change_filename_extension(current_filename: str, new_extension: str) -> str:
    current = sanitize_filename(current_filename)
    stem = PurePosixPath(current).stem or current
    ext = _reject_filename_part(new_extension, "Расширение файла")
    ext = ext[1:] if ext.startswith(".") else ext
    if not ext or "." in ext:
        raise FilenameEditError("Расширение файла должно быть одним суффиксом, например pdf")
    if len(ext) > 32:
        raise FilenameEditError("Расширение файла слишком длинное")
    safe_ext = sanitize_filename(ext, default="")
    if safe_ext != ext:
        raise FilenameEditError("Расширение файла содержит недопустимые символы")
    result = sanitize_filename(f"{stem}.{safe_ext}")
    if result != f"{stem}.{safe_ext}" or len(result) > 255:
        raise FilenameEditError("Итоговое имя файла слишком длинное или небезопасное")
    return result
