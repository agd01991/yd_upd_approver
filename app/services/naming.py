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


_FOLDER_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_FOLDER_BAD = re.compile(r"[^A-Za-z0-9А-Яа-яЁё№._ ()-]+")
MAX_FOLDER_SEGMENT_LENGTH = 180


class FolderNameValidationError(ValueError):
    """Ошибка безопасного имени папки пользователя."""


def build_recommended_user_folder_name(
    contract_number: str, contract_date: str, full_name: str
) -> str:
    return validate_user_folder_name(
        f"{contract_number.strip()} от {contract_date.strip()} {full_name.strip()}"
    )


def sanitize_user_folder_name(name: str) -> str:
    candidate = (name or "").strip()
    candidate = _FOLDER_CONTROL.sub("", candidate).replace("/", "_").replace("\\", "_")
    candidate = _FOLDER_BAD.sub("_", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" .")
    return candidate


def validate_user_folder_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        raise FolderNameValidationError("Имя папки не может быть пустым")
    lowered = raw.lower()
    if lowered.startswith("disk:/") or ":/" in raw:
        raise FolderNameValidationError("Введите только имя папки, не полный путь")
    if raw in {".", ".."} or ".." in raw:
        raise FolderNameValidationError("Имя папки не должно содержать переходы по пути")
    if _FOLDER_CONTROL.search(raw) or "/" in raw or "\\" in raw:
        raise FolderNameValidationError("Имя папки содержит недопустимые символы")
    safe = sanitize_user_folder_name(raw)
    if safe != raw or not safe:
        raise FolderNameValidationError("Имя папки содержит недопустимые символы")
    if len(safe) > MAX_FOLDER_SEGMENT_LENGTH:
        raise FolderNameValidationError("Имя папки слишком длинное")
    return safe


def user_folder_for_user(root: str, user) -> str:
    if getattr(user, "folder_name", None):
        return f"{root.rstrip('/')}/{validate_user_folder_name(user.folder_name)}/"
    return user_folder(root, user.telegram_id, user.full_name, user.username)
