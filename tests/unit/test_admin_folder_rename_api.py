import pytest
from fastapi import HTTPException

from app.api.routes.admin import admin_folder_rename_requests

pytestmark = pytest.mark.anyio


async def test_folder_rename_requests_bad_status_returns_400() -> None:
    with pytest.raises(HTTPException) as exc:
        await admin_folder_rename_requests(status="bad", session=None)
    assert exc.value.status_code == 400
    assert exc.value.detail == "Неизвестный статус заявки"
