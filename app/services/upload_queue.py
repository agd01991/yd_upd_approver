from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UploadMode, UploadRequest, UploadStatus, User
from app.services.audit import write_audit
from app.services.naming import copy_filename, join_disk_path


class UploadQueueError(ValueError):
    def __init__(self, message: str, code: str = "invalid_request_state") -> None:
        super().__init__(message)
        self.code = code


ACTION_TO_MODE = {
    "approve": UploadMode.normal,
    "copy": UploadMode.copy,
    "overwrite": UploadMode.overwrite,
}


def _same_mode(action: str, request: UploadRequest) -> UploadMode:
    if action == "retry":
        return request.upload_mode or UploadMode.normal
    return ACTION_TO_MODE[action]


def _ensure_temp_file(request: UploadRequest) -> None:
    if not request.local_path or not Path(request.local_path).is_file():
        raise UploadQueueError(
            "Временный файл не найден. Пользователь должен загрузить файл снова."
        )


async def enqueue_upload_request(
    session: AsyncSession,
    request_id: int,
    action: str,
    actor_telegram_id: int,
) -> UploadRequest:
    if action not in {"approve", "copy", "overwrite", "retry"}:
        raise UploadQueueError("Неизвестное действие.", "not_found")

    result = await session.execute(
        select(UploadRequest).where(UploadRequest.id == request_id).with_for_update()
    )
    request = result.scalar_one_or_none()
    if request is None:
        raise UploadQueueError("Заявка не найдена.", "request_not_found")

    user = await session.get(User, request.user_id)
    if user is None:
        raise UploadQueueError("Пользователь заявки не найден.", "request_not_found")

    mode = _same_mode(action, request)
    if request.status == UploadStatus.approved:
        if request.upload_mode == mode:
            return request
        raise UploadQueueError("Заявка уже стоит в очереди с другим режимом загрузки.")
    if request.status == UploadStatus.uploading:
        raise UploadQueueError("Заявка уже загружается worker-процессом.")
    if request.status in {UploadStatus.uploaded, UploadStatus.rejected}:
        raise UploadQueueError("Эта заявка уже обработана.")
    if action == "retry" and request.status != UploadStatus.failed:
        raise UploadQueueError("Повтор доступен только для заявок с ошибкой.")
    if action != "retry" and request.status not in {
        UploadStatus.pending_approval,
        UploadStatus.failed,
    }:
        raise UploadQueueError("Недопустимое состояние заявки.")

    _ensure_temp_file(request)
    now = datetime.now(UTC)
    old_status = request.status.value
    if mode == UploadMode.copy:
        request.target_path = join_disk_path(
            request.target_folder, copy_filename(request.safe_filename, request.request_code)
        )
    request.status = UploadStatus.approved
    request.upload_mode = mode
    request.queued_at = now
    request.approved_at = request.approved_at or now
    request.approved_by = actor_telegram_id
    request.error_message = None
    request.worker_token = None
    request.lease_expires_at = None
    await write_audit(
        session,
        actor_telegram_id=actor_telegram_id,
        action=f"upload_{action}",
        request_id=request.id,
        user_id=request.user_id,
        old_value={"status": old_status},
        new_value={
            "status": request.status.value,
            "upload_mode": mode.value,
            "queued_at": now.isoformat(),
        },
    )
    await session.commit()
    return request
