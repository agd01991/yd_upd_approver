from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import bot_dep, current_user_dep, get_db, settings_dep
from app.bot.keyboards import upload_keyboard
from app.config import Settings
from app.db.models import User, UserStatus
from app.db.repositories import create_upload_request, list_user_requests, next_request_code
from app.services.file_policy import validate_size
from app.services.naming import join_disk_path, sanitize_filename
from app.services.storage import TempStorage
from app.utils.formatting import format_upload_card

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
    bot=Depends(bot_dep),
) -> dict:
    user, _ = current
    if user.status != UserStatus.active or not user.root_folder:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only active users can upload files")
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
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File is too large")
            out.write(chunk)
    target_path = join_disk_path(user.root_folder, safe)
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
        target_folder=user.root_folder,
        target_path=target_path,
    )
    await session.commit()
    if bot:
        card = format_upload_card(upload, user)
        for admin_id in settings.telegram_admin_ids:
            await bot.send_message(admin_id, card, reply_markup=upload_keyboard(upload.id))
    return {"request_code": upload.request_code, "status": upload.status.value}
