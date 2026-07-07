from pathlib import Path
from typing import Any

import httpx

from app.services.naming import copy_filename, join_disk_path

API = "https://cloud-api.yandex.net/v1/disk/resources"


class YandexDiskError(RuntimeError):
    pass


class ConflictError(YandexDiskError):
    pass


class YandexDiskClient:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._own = client is None
        self.client = client or httpx.AsyncClient(timeout=60)
        self.headers = {"Authorization": f"OAuth {token}"}

    async def close(self) -> None:
        if self._own:
            await self.client.aclose()

    async def get_info(self, path: str) -> dict[str, Any]:
        response = await self.client.get(API, params={"path": path}, headers=self.headers)
        if response.status_code == 404:
            raise FileNotFoundError(path)
        response.raise_for_status()
        return response.json()

    async def exists(self, path: str) -> bool:
        try:
            await self.get_info(path)
        except FileNotFoundError:
            return False
        return True

    async def mkdir(self, path: str) -> None:
        response = await self.client.put(API, params={"path": path}, headers=self.headers)
        if response.status_code not in {201, 409}:
            response.raise_for_status()

    async def mkdir_recursive(self, path: str) -> None:
        prefix, tail = path.split(":/", 1)
        current = f"{prefix}:"
        for part in [p for p in tail.split("/") if p]:
            current = f"{current}/{part}"
            await self.mkdir(current)

    async def list_files(self, path: str, limit: int = 50) -> list[dict[str, Any]]:
        response = await self.client.get(
            API, params={"path": path, "limit": limit}, headers=self.headers
        )
        if response.status_code == 404:
            raise FileNotFoundError(path)
        response.raise_for_status()
        return response.json().get("_embedded", {}).get("items", [])

    async def get_upload_url(self, path: str, overwrite: bool = False) -> str:
        response = await self.client.get(
            f"{API}/upload",
            params={"path": path, "overwrite": str(overwrite).lower()},
            headers=self.headers,
        )
        if response.status_code == 409:
            raise ConflictError(path)
        response.raise_for_status()
        return response.json()["href"]

    async def upload_file(self, local_path: str, target_path: str, overwrite: bool = False) -> None:
        upload_url = await self.get_upload_url(target_path, overwrite=overwrite)
        with Path(local_path).open("rb") as file:
            response = await self.client.put(upload_url, content=file)
        response.raise_for_status()

    async def resolve_conflict_copy(self, folder: str, filename: str, request_code: str) -> str:
        return join_disk_path(folder, copy_filename(filename, request_code))
