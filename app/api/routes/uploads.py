import asyncio
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
from app.services.commit_safety import cleanup_staged_if_unowned, commit_cancellation_safe
from app.services.naming import join_disk_path, sanitize_filename
from app.services.storage import TempStorage
from app.services.telegram_outbox import enqueue_admin_upload_pending
from app.services.user_folders import resolve_user_folder_for_current_root

_ORIGINAL_RESOLVE_USER_FOLDER = resolve_user_folder_for_current_root
ensure_user_folder_for_current_root = resolve_user_folder_for_current_root


async def _resolve_target_folder(session, user, settings):  # noqa: ANN001
    if resolve_user_folder_for_current_root is not _ORIGINAL_RESOLVE_USER_FOLDER:
        resolver = resolve_user_folder_for_current_root
    else:
        resolver = ensure_user_folder_for_current_root
    try:
        return await resolver(session, user, settings)
    except TypeError:
        return await resolver(session, user, settings, None)


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

    async def chunks():
        try:
            while chunk := await file.read(1024 * 1024):
                yield chunk
        finally:
            close = getattr(file, "close", None)
            if close:
                result = close()
                if hasattr(result, "__await__"):
                    await result

    try:
        stored = await storage.save_async_chunks(
            request_code, safe, chunks(), max_bytes=settings.max_file_size_bytes
        )
    except ValueError as exc:
        raise ApiError(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "file_too_large",
            "Файл превышает допустимый размер.",
        ) from exc
    destination = stored.path
    commit_started = False
    try:
        target_folder = await _resolve_target_folder(session, user, settings)
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
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            caption=caption,
            local_path=str(destination),
            target_folder=target_folder,
            target_path=target_path,
            source_event_key=source_event_key,
        )
        await enqueue_admin_upload_pending(session, settings, upload, user)
        commit_started = True
        outcome = await commit_cancellation_safe(session)
        if outcome.cancelled:
            raise asyncio.CancelledError()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(
            select(UploadRequest).where(
                UploadRequest.source_event_key == source_event_key, UploadRequest.user_id == user.id
            )
        )
        if existing:
            if str(getattr(existing, "local_path", "")) != str(destination):
                storage.delete_safe(destination)
            return {"request_code": existing.request_code, "status": existing.status.value}
        await cleanup_staged_if_unowned(
            storage,
            destination,
            source_event_key=source_event_key,
            user_id=user.id,
            request_code=request_code,
        )
        raise
    except BaseException:
        if not commit_started:
            await session.rollback()
            storage.delete_safe(destination)
        else:
            await cleanup_staged_if_unowned(
                storage,
                destination,
                source_event_key=source_event_key,
                user_id=user.id,
                request_code=request_code,
            )
        raise
    return {"request_code": upload.request_code, "status": upload.status.value}
