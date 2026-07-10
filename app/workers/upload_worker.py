from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UploadRequest, UploadStatus
from app.services.approval import require_transition
from app.services.storage import TempStorage
from app.services.yandex_disk import (
    ConflictError,
    InsufficientStorageError,
    YandexAuthError,
    YandexDiskClient,
    YandexNetworkError,
)


async def upload_approved_request(
    session: AsyncSession, request: UploadRequest, client: YandexDiskClient, overwrite: bool = False
) -> None:
    if request.status != UploadStatus.approved:
        require_transition(request.status, UploadStatus.uploading)
    if not request.local_path or not Path(request.local_path).exists():
        request.status = UploadStatus.failed
        request.error_message = "Temporary file not found. User must upload the file again."
        await session.flush()
        return

    request.status = UploadStatus.uploading
    request.error_message = None
    await session.flush()
    try:
        await client.mkdir_recursive(request.target_folder)
        await client.upload_file(request.local_path, request.target_path, overwrite=overwrite)
    except ConflictError:
        request.status = UploadStatus.failed
        request.error_message = "Name conflict on Yandex Disk"
        await session.flush()
        return
    except (YandexAuthError, YandexNetworkError):
        request.status = UploadStatus.failed
        request.error_message = "Яндекс.Диск временно недоступен. Повторите попытку позже."
        await session.flush()
        return
    except InsufficientStorageError:
        request.status = UploadStatus.failed
        request.error_message = "На Яндекс.Диске недостаточно свободного места."
        await session.flush()
        return
    except Exception:
        request.status = UploadStatus.failed
        request.error_message = "Не удалось загрузить файл. Повторите попытку позже."
        await session.flush()
        return
    request.status = UploadStatus.uploaded
    request.uploaded_at = datetime.now(UTC)
    TempStorage.delete(request.local_path)
    await session.flush()
