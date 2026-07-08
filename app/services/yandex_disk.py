from pathlib import Path
from typing import Any

import httpx

from app.services.naming import copy_filename, join_disk_path

API = "https://cloud-api.yandex.net/v1/disk/resources"


class YandexDiskError(RuntimeError):
    """Base Yandex Disk client error."""


class YandexAuthError(YandexDiskError):
    """OAuth token is invalid or lacks required permissions."""


class ConflictError(YandexDiskError):
    """Target path conflicts with an existing resource."""


class InsufficientStorageError(YandexDiskError):
    """Yandex Disk has no free space for the requested upload."""


class YandexNetworkError(YandexDiskError):
    """Network-level error while calling Yandex Disk API."""


class YandexDiskClient:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._own = client is None
        self.client = client or httpx.AsyncClient(timeout=60)
        self.headers = {"Authorization": f"OAuth {token}"}

    async def close(self) -> None:
        if self._own:
            await self.client.aclose()

    @staticmethod
    def _message(response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return response.text or response.reason_phrase
        return data.get("message") or data.get("description") or response.reason_phrase

    def _raise_for_status(self, response: httpx.Response, path: str | None = None) -> None:
        if response.status_code in {401, 403}:
            msg = "Yandex Disk token is invalid or does not have enough permissions"
            raise YandexAuthError(msg)
        if response.status_code == 404:
            raise FileNotFoundError(path or self._message(response))
        if response.status_code == 409:
            raise ConflictError(path or self._message(response))
        if response.status_code == 507:
            raise InsufficientStorageError("Not enough free space on Yandex Disk")
        response.raise_for_status()

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self.client.request(method, url, headers=self.headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise YandexNetworkError("Yandex Disk request timed out") from exc
        except httpx.NetworkError as exc:
            raise YandexNetworkError("Yandex Disk network error") from exc

    async def get_info(self, path: str) -> dict[str, Any]:
        response = await self._request("GET", API, params={"path": path})
        self._raise_for_status(response, path)
        return response.json()

    async def exists(self, path: str) -> bool:
        try:
            await self.get_info(path)
        except FileNotFoundError:
            return False
        return True

    async def mkdir(self, path: str) -> None:
        response = await self._request("PUT", API, params={"path": path})
        if response.status_code == 409:
            return
        self._raise_for_status(response, path)

    async def mkdir_recursive(self, path: str) -> None:
        prefix, tail = path.split(":/", 1)
        current = f"{prefix}:"
        for part in [p for p in tail.split("/") if p]:
            current = f"{current}/{part}"
            await self.mkdir(current)

    async def list_files(self, path: str, limit: int = 50) -> list[dict[str, Any]]:
        response = await self._request("GET", API, params={"path": path, "limit": limit})
        self._raise_for_status(response, path)
        return response.json().get("_embedded", {}).get("items", [])

    async def get_upload_url(self, path: str, overwrite: bool = False) -> str:
        response = await self._request(
            "GET",
            f"{API}/upload",
            params={"path": path, "overwrite": str(overwrite).lower()},
        )
        self._raise_for_status(response, path)
        return response.json()["href"]

    async def upload_file(self, local_path: str, target_path: str, overwrite: bool = False) -> None:
        upload_url = await self.get_upload_url(target_path, overwrite=overwrite)
        content = Path(local_path).read_bytes()
        try:
            response = await self.client.put(upload_url, content=content)
        except httpx.TimeoutException as exc:
            raise YandexNetworkError("Yandex Disk upload timed out") from exc
        except httpx.NetworkError as exc:
            raise YandexNetworkError("Yandex Disk upload network error") from exc
        self._raise_for_status(response, target_path)

    async def resolve_conflict_copy(self, folder: str, filename: str, request_code: str) -> str:
        return join_disk_path(folder, copy_filename(filename, request_code))
