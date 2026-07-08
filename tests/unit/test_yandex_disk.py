import httpx
import pytest

from app.services.yandex_disk import ConflictError, YandexDiskClient


@pytest.mark.anyio
async def test_yandex_client_upload_and_list(tmp_path) -> None:
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if str(request.url).startswith("https://cloud-api.yandex.net/v1/disk/resources/upload"):
            return httpx.Response(200, json={"href": "https://upload.example/put"})
        if str(request.url).startswith("https://upload.example/put"):
            assert await request.aread() == b"hello"
            return httpx.Response(201)
        if request.method == "GET":
            return httpx.Response(200, json={"_embedded": {"items": [{"name": "a.txt"}]}})
        return httpx.Response(201)

    path = tmp_path / "a.txt"
    path.write_text("hello")
    client = YandexDiskClient("token", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await client.upload_file(str(path), "disk:/root/a.txt")
    files = await client.list_files("disk:/root")
    assert files[0]["name"] == "a.txt"
    assert any(method == "PUT" and url == "https://upload.example/put" for method, url in calls)


@pytest.mark.anyio
async def test_yandex_upload_streams_without_read_bytes(tmp_path, monkeypatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://cloud-api.yandex.net/v1/disk/resources/upload"):
            return httpx.Response(200, json={"href": "https://upload.example/put"})
        assert await request.aread() == b"streamed"
        return httpx.Response(201)

    def fail_read_bytes(self):
        raise AssertionError("upload_file must not read the whole file with Path.read_bytes()")

    monkeypatch.setattr("pathlib.Path.read_bytes", fail_read_bytes)
    path = tmp_path / "streamed.txt"
    path.write_bytes(b"streamed")
    client = YandexDiskClient("token", httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    await client.upload_file(str(path), "disk:/root/streamed.txt")


@pytest.mark.anyio
async def test_yandex_conflict() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409)

    client = YandexDiskClient("token", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ConflictError):
        await client.get_upload_url("disk:/root/a.txt")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "YandexAuthError"),
        (403, "YandexAuthError"),
        (404, "FileNotFoundError"),
        (409, "ConflictError"),
        (507, "InsufficientStorageError"),
    ],
)
async def test_yandex_error_mapping(status: int, expected: str) -> None:
    from app.services import yandex_disk

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"message": "error"})

    client = YandexDiskClient("token", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(getattr(yandex_disk, expected, FileNotFoundError)):
        await client.list_files("disk:/missing")
