from urllib.parse import urlsplit


class DiskPathValidationError(ValueError):
    pass


def validate_yandex_disk_root(root: str) -> str:
    value = root.strip() if root else ""
    if not value:
        raise DiskPathValidationError("Yandex Disk root must not be empty")
    if ":\\" in value or "\\" in value:
        raise DiskPathValidationError("Yandex Disk root must be a disk:/ path")
    parsed = urlsplit(value)
    if parsed.scheme and parsed.scheme != "disk":
        raise DiskPathValidationError("Yandex Disk root must use disk:/ scheme")
    if not value.startswith(("disk:/", "disk://")):
        raise DiskPathValidationError("Yandex Disk root must start with disk:/")

    path = value.removeprefix("disk://").removeprefix("disk:/")
    parts = [part for part in path.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise DiskPathValidationError("Yandex Disk root must not contain path traversal")
    return "disk:/" + "/".join(parts)
