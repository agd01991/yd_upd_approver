import re

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user_dep, get_db, settings_dep
from app.api.errors import ApiError
from app.config import Settings
from app.db.models import UploadRequest, User, UserStatus
from app.db.repositories import create_upload_request, list_user_requests, next_request_code
from app.services.file_policy import validate_size
from app.services.naming import join_disk_path, sanitize_filename
from app.services.storage import TempStorage
from app.services.telegram_outbox import enqueue_admin_upload_pending
from app.services.user_folders import ensure_user_folder_for_current_root
from app.services.yandex_disk import YandexDiskClient

router = APIRouter(prefix="/uploads")


def upload_json(upload) -> dict:
    return {
        k: getattr(upload, k, None)
        for k in [
            "id",
            "request_code",
            "original_filename",
            "safe_filename",
            "size_bytes",
            "sha256",
            "caption",
            "error_message",
            "reject_reason",
            "created_at",
            "uploaded_at",
        ]
    } | {"status": upload.status.value}


@router.get("")
async def list_uploads(
    current: tuple[User, bool] = Depends(current_user_dep), session: AsyncSession = Depends(get_db)
) -> list[dict]:
    user, _ = current
    await session.commit()
    return [upload_json(r) for r in await list_user_requests(session, user.id, limit=50)]


@router.post("")
async def create_upload(
    file: UploadFile = File(...),
    caption: str | None = Form(default=None),
    current: tuple[User, bool] = Depends(current_user_dep),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    user, _ = current
    if user.status != UserStatus.active or not user.root_folder:
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "user_not_active",
            "Загрузка доступна только активным пользователям.",
        )
    if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency_key):
        raise ApiError(
            400, "invalid_idempotency_key", "Передайте корректный заголовок Idempotency-Key."
        )
    source_event_key = f"mini-app-upload:{user.id}:{idempotency_key}"
    existing = await session.scalar(
        select(UploadRequest).where(
            UploadRequest.source_event_key == source_event_key, UploadRequest.user_id == user.id
        )
    )
    if existing:
        return {"request_code": existing.request_code, "status": existing.status.value}

    safe = sanitize_filename(file.filename or "file")
    request_code = await next_request_code(session)
    storage = TempStorage(settings.temp_storage_dir)
    destination = storage.path_for(request_code, safe)
    size = 0
    with destination.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if not validate_size(size, settings):
                destination.unlink(missing_ok=True)
                raise ApiError(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    "file_too_large",
                    "Файл превышает допустимый размер.",
                )
            out.write(chunk)
    client = YandexDiskClient(settings.yandex_disk_token)
    try:
        target_folder = await ensure_user_folder_for_current_root(session, user, settings, client)
    except Exception as exc:
        if hasattr(session, "rollback"):
            await session.rollback()
        destination.unlink(missing_ok=True)
        raise ApiError(
            503, "yandex_disk_unavailable", "Не удалось подготовить папку на Яндекс.Диске"
        ) from exc
    finally:
        await client.close()
    target_path = join_disk_path(target_folder, safe)
    upload = await create_upload_request(
        session,
        request_code=request_code,
        user_id=user.id,
        source="mini_app",
        telegram_file_id=None,
        telegram_file_unique_id=None,
        original_filename=file.filename or safe,
        safe_filename=safe,
        mime_type=file.content_type,
        size_bytes=size,
        sha256=storage.sha256(destination),
        caption=caption,
        local_path=str(destination),
        target_folder=target_folder,
        target_path=target_path,
        source_event_key=source_event_key,
    )
    await enqueue_admin_upload_pending(session, settings, upload, user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        destination.unlink(missing_ok=True)
        existing = await session.scalar(
            select(UploadRequest).where(
                UploadRequest.source_event_key == source_event_key, UploadRequest.user_id == user.id
            )
        )
        if existing:
            return {"request_code": existing.request_code, "status": existing.status.value}
        raise
    return {"request_code": upload.request_code, "status": upload.status.value}
