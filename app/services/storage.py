import asyncio
import hashlib
import os
from collections.abc import AsyncIterable, Iterable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class StoredFile:
    path: Path
    size_bytes: int
    sha256: str


class StoragePathError(ValueError):
    pass


class TempStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_request_dir(self, request_code: str) -> Path:
        folder = (self.root / request_code).resolve(strict=False)
        folder.relative_to(self.root)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def path_for(self, request_code: str, filename: str) -> Path:
        folder = self._safe_request_dir(request_code)
        path = (folder / filename).resolve(strict=False)
        path.relative_to(self.root)
        return path

    def validate_inside(self, path: str | Path) -> Path:
        candidate = Path(path)
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise StoragePathError("path is outside temp storage") from exc
        return resolved

    def _cleanup_empty_request_dir(self, path: Path) -> None:
        parent = path.parent
        try:
            parent.relative_to(self.root)
        except ValueError:
            return
        if parent == self.root:
            return
        try:
            parent.rmdir()
        except OSError:
            pass

    async def save_async_chunks(
        self,
        request_code: str,
        filename: str,
        chunks: AsyncIterable[bytes],
        *,
        max_bytes: int | None = None,
    ) -> StoredFile:
        destination = self.path_for(request_code, filename)
        part = destination.with_name(f".{destination.name}.{uuid4().hex}.part")
        digest = hashlib.sha256()
        size = 0
        try:
            with part.open("xb") as out:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise ValueError("file exceeds max size")
                    digest.update(chunk)
                    await asyncio.to_thread(out.write, chunk)
            await asyncio.to_thread(os.replace, part, destination)
            return StoredFile(destination, size, digest.hexdigest())
        except BaseException:
            part.unlink(missing_ok=True)
            self._cleanup_empty_request_dir(destination)
            raise

    async def save_chunks(
        self,
        request_code: str,
        filename: str,
        chunks: Iterable[bytes],
        *,
        max_bytes: int | None = None,
    ) -> StoredFile:
        async def gen() -> AsyncIterable[bytes]:
            for chunk in chunks:
                yield chunk

        return await self.save_async_chunks(request_code, filename, gen(), max_bytes=max_bytes)

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def delete_safe(self, path: str | Path) -> bool:
        target = self.validate_inside(path)
        if target.is_symlink():
            target.unlink(missing_ok=True)
            self._cleanup_empty_request_dir(target)
            return True
        try:
            target.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        self._cleanup_empty_request_dir(target)
        return True

    @staticmethod
    def delete(path: str) -> None:
        Path(path).unlink(missing_ok=True)
