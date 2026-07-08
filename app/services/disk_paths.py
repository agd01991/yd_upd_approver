from pathlib import PurePosixPath


def validate_yandex_disk_root(root: str) -> str:
    value = (root or "").strip().replace("\\", "/")
    if not value or not value.startswith("disk:/") or value.startswith("disk:///"):
        msg = "Yandex Disk root must start with disk:/"
        raise ValueError(msg)
    if value.startswith("disk://"):
        value = "disk:/" + value.removeprefix("disk://")
    raw_path = value.removeprefix("disk:/")
    parts = [part for part in raw_path.split("/") if part]
    if not parts:
        return "disk:/"
    if any(part in {".", ".."} for part in parts):
        msg = "Yandex Disk root must not contain path traversal"
        raise ValueError(msg)
    normalized_path = PurePosixPath(*parts).as_posix()
    return f"disk:/{normalized_path}"
