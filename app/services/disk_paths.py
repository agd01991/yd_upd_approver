class DiskPathValidationError(ValueError):
    """Raised when a Yandex Disk path is unsafe or invalid."""


def validate_yandex_disk_root(candidate: str) -> str:
    """Validate and normalize a Yandex Disk root path.

    Empty path segments are collapsed so admin input such as ``disk://Root``
    and ``disk:/Root//Child`` is stored as a single canonical path.
    """
    if not candidate or not candidate.strip():
        raise DiskPathValidationError("Yandex Disk root path must not be empty")

    value = candidate.strip()
    if not value.startswith("disk:/"):
        raise DiskPathValidationError("Yandex Disk root path must start with disk:/")

    rest = value.removeprefix("disk:/")
    parts = [part.strip() for part in rest.split("/") if part.strip()]
    if not parts:
        raise DiskPathValidationError("Yandex Disk root path must contain a folder name")
    if any(part == ".." for part in parts):
        raise DiskPathValidationError("Yandex Disk root path must not contain '..' segments")

    return "disk:/" + "/".join(parts)
