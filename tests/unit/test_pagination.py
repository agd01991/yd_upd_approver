from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api.errors import ApiError
from app.api.pagination import MAX_DATABASE_ID, decode_cursor, encode_cursor, page_response


def test_cursor_round_trip_with_tie_breaker() -> None:
    created_at = datetime(2026, 7, 16, tzinfo=UTC)
    cursor = encode_cursor(created_at, 42)
    assert decode_cursor(cursor) == (created_at, 42)


@pytest.mark.parametrize("row_id", [1, MAX_DATABASE_ID, "1", str(MAX_DATABASE_ID)])
def test_cursor_accepts_postgresql_integer_boundaries_and_legacy_strings(row_id) -> None:
    created_at = datetime(2026, 7, 16, tzinfo=UTC)
    raw = encode_cursor(created_at, 1)
    import base64
    import json

    payload = {"created_at": created_at.isoformat(), "id": row_id}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    assert decode_cursor(raw) == (created_at, int(row_id))


@pytest.mark.parametrize("row_id", [0, -1, MAX_DATABASE_ID + 1, 10**100, True, False, 1.5, "1.5"])
def test_cursor_rejects_values_outside_postgresql_integer_domain(row_id) -> None:
    import base64
    import json

    payload = {"created_at": datetime(2026, 7, 16, tzinfo=UTC).isoformat(), "id": row_id}
    cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    with pytest.raises(ApiError) as exc:
        decode_cursor(cursor)
    assert (exc.value.status_code, exc.value.code) == (400, "invalid_cursor")


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
