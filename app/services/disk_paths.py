class DiskPathValidationError(ValueError):
    pass


def validate_yandex_disk_root(root: str) -> str:
    if not isinstance(root, str):
        raise DiskPathValidationError("Path must be a string")
    value = root.strip()
    if not value:
        raise DiskPathValidationError("Path must not be empty")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise DiskPathValidationError("Path must not contain control characters")
    if "\\" in value:
        raise DiskPathValidationError("Path must not contain backslashes")
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "file://")) or ":\\" in value:
        raise DiskPathValidationError("Only Yandex Disk paths are supported")
    if not value.startswith(("disk:/", "disk://")):
        raise DiskPathValidationError("Path must start with disk:/")
    tail = value.split(":/", 1)[1]
    parts = [part for part in tail.split("/") if part]
    if not parts:
        raise DiskPathValidationError("Path must include a folder")
    if any(part in {".", ".."} for part in parts):
        raise DiskPathValidationError("Path traversal is not allowed")
    return "disk:/" + "/".join(parts)
