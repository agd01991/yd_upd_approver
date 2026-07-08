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
