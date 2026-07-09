from pathlib import PurePosixPath


class DiskPathValidationError(ValueError):
    pass


def validate_yandex_disk_root(root: str) -> str:
    value = root.strip()
    if not value:
        raise DiskPathValidationError("Yandex Disk root cannot be empty")
    if "\\" in value or "://" in value and not value.startswith("disk://"):
        raise DiskPathValidationError("Yandex Disk root must start with disk:/")
    if not (value.startswith("disk:/") or value.startswith("disk://")):
        raise DiskPathValidationError("Yandex Disk root must start with disk:/")

    path = value.removeprefix("disk:")
    while path.startswith("//"):
        path = path[1:]
    if not path.startswith("/"):
        raise DiskPathValidationError("Yandex Disk root must be absolute")

    parts = [part for part in path.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise DiskPathValidationError("Yandex Disk root cannot contain traversal segments")
    normalized = PurePosixPath("/", *parts).as_posix()
    if normalized == "/":
        raise DiskPathValidationError("Yandex Disk root must include a folder")
    return f"disk:{normalized}"
