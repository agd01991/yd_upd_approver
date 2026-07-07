from app.db.repositories import next_request_code


class FakeSession:
    def __init__(self, value: int | None) -> None:
        self.value = value

    async def scalar(self, statement):  # noqa: ANN001
        return self.value


def test_request_code_format() -> None:
    # Repository uses max id + 1, so usual sequence remains human readable.
    import asyncio

    assert asyncio.run(next_request_code(FakeSession(None))) == "REQ-000001"
    assert asyncio.run(next_request_code(FakeSession(41))) == "REQ-000042"
