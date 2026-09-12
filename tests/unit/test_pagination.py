from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api.errors import ApiError
from app.api.pagination import decode_cursor, encode_cursor, page_response


def test_cursor_round_trip_with_tie_breaker() -> None:
    created_at = datetime(2026, 7, 16, tzinfo=UTC)
    cursor = encode_cursor(created_at, 42)
    assert decode_cursor(cursor) == (created_at, 42)


@pytest.mark.parametrize(
    "cursor", ["not-base64", "x" * 513, encode_cursor(datetime(2026, 7, 16, tzinfo=UTC), 0)]
)
def test_invalid_cursor_uses_api_error_contract(cursor: str) -> None:
    with pytest.raises(ApiError) as exc:
        decode_cursor(cursor)
    assert exc.value.status_code == 400
    assert exc.value.code == "invalid_cursor"


def test_page_response_fetches_limit_plus_one_without_count() -> None:
    rows = [
        SimpleNamespace(id=3, created_at=datetime(2026, 7, 16, 3, tzinfo=UTC)),
        SimpleNamespace(id=2, created_at=datetime(2026, 7, 16, 2, tzinfo=UTC)),
        SimpleNamespace(id=1, created_at=datetime(2026, 7, 16, 1, tzinfo=UTC)),
    ]
    page = page_response(rows, 2, lambda row: {"id": row.id})
    assert page["items"] == [{"id": 3}, {"id": 2}]
    assert page["limit"] == 2
    assert page["has_more"] is True
    assert decode_cursor(page["next_cursor"]) == (rows[1].created_at, 2)
