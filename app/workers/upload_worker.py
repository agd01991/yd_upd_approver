from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UploadRequest, UploadStatus
from app.services.approval import require_transition
from app.services.storage import TempStorage
from app.services.yandex_disk import ConflictError, YandexDiskClient


async def upload_approved_request(
    session: AsyncSession, request: UploadRequest, client: YandexDiskClient, overwrite: bool = False
) -> None:
    require_transition(request.status, UploadStatus.uploading)
    request.status = UploadStatus.uploading
    await session.flush()
    try:
        await client.mkdir_recursive(request.target_folder)
        await client.upload_file(request.local_path, request.target_path, overwrite=overwrite)
    except ConflictError:
        request.status = UploadStatus.failed
        request.error_message = "Name conflict on Yandex Disk"
        await session.flush()
        return
    except Exception as exc:
        request.status = UploadStatus.failed
        request.error_message = str(exc)
        await session.flush()
        return
    request.status = UploadStatus.uploaded
    request.uploaded_at = datetime.now(UTC)
    TempStorage.delete(request.local_path)
    await session.flush()
