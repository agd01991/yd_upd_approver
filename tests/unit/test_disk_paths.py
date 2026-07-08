import pytest

from app.services.disk_paths import validate_yandex_disk_root


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("disk://Root", "disk:/Root"),
        ("disk:/Root//Child", "disk:/Root/Child"),
        ("disk:/Root/Child/", "disk:/Root/Child"),
    ],
)
def test_validate_yandex_disk_root_normalizes(raw: str, expected: str) -> None:
    assert validate_yandex_disk_root(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "disk:/../secret",
        "disk:/Root/../secret",
        "disk:/Root/..",
        "https://example.com",
        r"C:\Users\test",
        "",
    ],
)
def test_validate_yandex_disk_root_rejects_unsafe_paths(raw: str) -> None:
    with pytest.raises(ValueError):
        validate_yandex_disk_root(raw)
