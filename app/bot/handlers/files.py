from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import upload_keyboard
from app.config import Settings
from app.db.repositories import create_upload_request, get_user_by_tg
from app.services.file_policy import can_user_upload, validate_size
from app.services.naming import join_disk_path, sanitize_filename

router = Router()


@router.message(lambda message: bool(message.document))
async def document_upload(message: Message, session: AsyncSession, settings: Settings) -> None:
    user = await get_user_by_tg(session, message.from_user.id)
    if not user or not can_user_upload(user):
        await message.answer("Загрузка доступна только после одобрения администратора.")
        return
    document = message.document
    if not validate_size(document.file_size or 0, settings):
        await message.answer("Файл слишком большой.")
        return
    safe = sanitize_filename(document.file_name or "file")
    target_folder = user.root_folder or settings.yandex_disk_root
    target_path = join_disk_path(target_folder, safe)
    upload = await create_upload_request(
        session,
        user_id=user.id,
        telegram_file_id=document.file_id,
        telegram_file_unique_id=document.file_unique_id,
        original_filename=document.file_name or safe,
        safe_filename=safe,
        mime_type=document.mime_type,
        size_bytes=document.file_size or 0,
        sha256="0" * 64,
        caption=message.caption,
        local_path="pending_telegram_download",
        target_folder=target_folder,
        target_path=target_path,
    )
    await session.commit()
    await message.answer(
        f"Файл ожидает проверки: {upload.request_code}", reply_markup=upload_keyboard(upload.id)
    )
