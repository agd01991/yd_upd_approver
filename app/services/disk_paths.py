import re

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:\\")


class DiskPathValidationError(ValueError):
    pass


def validate_yandex_disk_root(path: str) -> str:
    candidate = (path or "").strip()
    if not candidate:
        msg = "Путь не может быть пустым"
        raise DiskPathValidationError(msg)
    if _CONTROL.search(candidate):
        msg = "Путь не должен содержать управляющие символы"
        raise DiskPathValidationError(msg)
    if "\\" in candidate:
        msg = "Путь не должен содержать обратный слеш"
        raise DiskPathValidationError(msg)
    if _WINDOWS_DRIVE.match(candidate) or candidate.startswith("/"):
        msg = "Укажите путь Яндекс.Диска в формате disk:/Folder"
        raise DiskPathValidationError(msg)
    if _SCHEME.match(candidate) and not candidate.startswith("disk:/"):
        msg = "Поддерживаются только пути Яндекс.Диска disk:/..."
        raise DiskPathValidationError(msg)
    if not candidate.startswith("disk:/"):
        msg = "Путь должен начинаться с disk:/"
        raise DiskPathValidationError(msg)
    normalized = candidate.rstrip("/")
    rest = normalized.removeprefix("disk:/")
    if not rest:
        msg = "После disk:/ должна быть непустая папка"
        raise DiskPathValidationError(msg)
    parts = rest.split("/")
    if any(part == ".." for part in parts):
        msg = "Path traversal запрещён"
        raise DiskPathValidationError(msg)
    return normalized
