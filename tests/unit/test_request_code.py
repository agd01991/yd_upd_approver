import re

import pytest

from app.db.repositories import next_request_code


class FakeSession:
    def __init__(self, existing: bool = False) -> None:
        self.existing = existing
        self.calls = 0

    async def scalar(self, statement):  # noqa: ANN001
        self.calls += 1
        if self.existing and self.calls == 1:
            return 1
        return None


@pytest.mark.anyio
async def test_request_code_format() -> None:
    code = await next_request_code(FakeSession())
    assert re.fullmatch(r"REQ-\d{8}-[A-F0-9]{8}", code)


@pytest.mark.anyio
async def test_request_code_retries_on_collision() -> None:
    session = FakeSession(existing=True)
    code = await next_request_code(session)
    assert re.fullmatch(r"REQ-\d{8}-[A-F0-9]{8}", code)
    assert session.calls == 2
