import pytest

from app.config import Settings
from app.services.disk_paths import DiskPathValidationError, validate_yandex_disk_root


def test_admin_ids_accept_string_and_iterables() -> None:
    assert Settings(telegram_admin_ids="123, 456").telegram_admin_ids == [123, 456]
    assert Settings(telegram_admin_ids=[123, "456"]).telegram_admin_ids == [123, 456]
    assert Settings(telegram_admin_ids=(123, "456")).telegram_admin_ids == [123, 456]
    assert Settings(telegram_admin_ids=(value for value in ["123", 456])).telegram_admin_ids == [
        123,
        456,
    ]


def test_cors_origins_accept_string_and_iterables() -> None:
    assert Settings(cors_origins="https://a.example, https://b.example").cors_origins == [
        "https://a.example",
        "https://b.example",
    ]
    assert Settings(cors_origins=["https://a.example", " https://b.example "]).cors_origins == [
        "https://a.example",
        "https://b.example",
    ]
    assert Settings(cors_origins=("https://a.example", " https://b.example ")).cors_origins == [
        "https://a.example",
        "https://b.example",
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("disk://Root", "disk:/Root"),
        ("disk:/Root//Child", "disk:/Root/Child"),
        ("disk:/Root/Child/", "disk:/Root/Child"),
    ],
)
def test_validate_yandex_disk_root_normalizes_empty_segments(raw: str, expected: str) -> None:
    assert validate_yandex_disk_root(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "disk:/../secret",
        "disk:/Root/../secret",
        "disk:/Root/..",
        "",
        "https://example.com",
        r"C:\Users\test",
    ],
)
def test_validate_yandex_disk_root_rejects_unsafe_paths(raw: str) -> None:
    with pytest.raises(DiskPathValidationError):
        validate_yandex_disk_root(raw)
