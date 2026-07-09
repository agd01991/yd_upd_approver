import pytest

from app.services.disk_paths import DiskPathValidationError, validate_yandex_disk_root


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("disk:/Telegram Uploads", "disk:/Telegram Uploads"),
        ("disk://Root", "disk:/Root"),
        ("disk:/Root//Child", "disk:/Root/Child"),
        ("disk:/Root/Child/", "disk:/Root/Child"),
        ("disk:/Загрузки пользователей", "disk:/Загрузки пользователей"),
    ],
)
def test_validate_yandex_disk_root_accepts_and_normalizes(value: str, expected: str) -> None:
    assert validate_yandex_disk_root(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/",
        "../secret",
        "disk:",
        "disk:/",
        "disk:/../secret",
        "disk:/Root/../secret",
        "disk:/Root/..",
        "https://example.com",
        "file:///tmp/test",
        r"C:\Users\test",
        "disk:/Root\\Child",
    ],
)
def test_validate_yandex_disk_root_rejects_unsafe_paths(value: str) -> None:
    with pytest.raises(DiskPathValidationError):
        validate_yandex_disk_root(value)
