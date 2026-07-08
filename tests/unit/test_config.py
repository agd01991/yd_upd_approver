from app.config import Settings


def test_admin_ids_accept_string_and_iterables() -> None:
    assert Settings(telegram_admin_ids="123, 456").telegram_admin_ids == [123, 456]
    assert Settings(telegram_admin_ids="[123, 456]").telegram_admin_ids == [123, 456]
    assert Settings(telegram_admin_ids=[123, "456"]).telegram_admin_ids == [123, 456]
    assert Settings(telegram_admin_ids=(123, "456")).telegram_admin_ids == [123, 456]
    assert Settings(telegram_admin_ids=(value for value in ["123", 456])).telegram_admin_ids == [
        123,
        456,
    ]


def test_cors_origins_accept_string_and_iterables() -> None:
    assert Settings(cors_origins='["https://a.example", "https://b.example"]').cors_origins == [
        "https://a.example",
        "https://b.example",
    ]
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
