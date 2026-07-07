import httpx
import pytest

from app.services.yandex_disk import ConflictError, YandexDiskClient


@pytest.mark.asyncio
async def test_yandex_client_upload_and_list(tmp_path) -> None:
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if str(request.url).startswith("https://cloud-api.yandex.net/v1/disk/resources/upload"):
            return httpx.Response(200, json={"href": "https://upload.example/put"})
        if str(request.url).startswith("https://upload.example/put"):
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


@pytest.mark.asyncio
async def test_yandex_conflict() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409)

    client = YandexDiskClient("token", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ConflictError):
        await client.get_upload_url("disk:/root/a.txt")
