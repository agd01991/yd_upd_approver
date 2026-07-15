import asyncio
import hashlib
import logging
import os
from collections.abc import AsyncIterable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

CHUNK_SIZE = 1024 * 1024
logger = logging.getLogger(__name__)
T = TypeVar("T")


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

    async def _run_blocking_file_op(
        self, operation: Callable[..., T], /, *args: object, cleanup_context: str
    ) -> T:
        task = asyncio.create_task(asyncio.to_thread(operation, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except BaseException as exc:
                logger.warning(
                    "Cancelled temp file operation finished with error: operation=%s category=%s",
                    cleanup_context,
                    exc.__class__.__name__,
                )
            raise
        except BaseException:
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException as exc:
                    logger.debug(
                        "Discarding failed temp file operation after primary error: operation=%s category=%s",
                        cleanup_context,
                        exc.__class__.__name__,
                    )
            raise

    async def _cleanup_after_save_failure(
        self, *, part: Path, destination: Path, renamed: bool
    ) -> None:
        def cleanup() -> None:
            target = destination if renamed else part
            target.unlink(missing_ok=True)
            self._cleanup_empty_request_dir(destination)

        try:
            await self._run_blocking_file_op(cleanup, cleanup_context="cleanup")
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            logger.warning(
                "Temp file cleanup failed: operation=save_async_chunks_cleanup category=%s",
                exc.__class__.__name__,
            )

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
        renamed = False
        try:
            out = await self._run_blocking_file_op(part.open, "xb", cleanup_context="open")
            try:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise ValueError("file exceeds max size")
                    digest.update(chunk)
                    await self._run_blocking_file_op(out.write, chunk, cleanup_context="write")
                await self._run_blocking_file_op(out.flush, cleanup_context="flush")
            finally:
                await self._run_blocking_file_op(out.close, cleanup_context="close")
            replace_task = asyncio.create_task(asyncio.to_thread(os.replace, part, destination))
            try:
                await asyncio.shield(replace_task)
                renamed = True
            except asyncio.CancelledError:
                try:
                    await replace_task
                    renamed = True
                except BaseException as exc:
                    logger.warning(
                        "Cancelled temp file operation finished with error: operation=replace category=%s",
                        exc.__class__.__name__,
                    )
                raise
            return StoredFile(destination, size, digest.hexdigest())
        except asyncio.CancelledError:
            await self._cleanup_after_save_failure(
                part=part, destination=destination, renamed=renamed
            )
            raise
        except BaseException:
            await self._cleanup_after_save_failure(
                part=part, destination=destination, renamed=renamed
            )
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
