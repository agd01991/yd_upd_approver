from aiogram import Bot, Router
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import UploadRequest, UserStatus
from app.db.repositories import create_upload_request, get_user_by_tg, next_request_code
from app.services.file_policy import validate_size
from app.services.naming import join_disk_path, sanitize_filename
from app.services.storage import TempStorage
from app.services.telegram_files import download_file
from app.services.telegram_outbox import enqueue_admin_upload_pending
from app.services.user_folders import ensure_user_folder_for_current_root
from app.services.yandex_disk import YandexDiskClient

router = Router()


@router.message(lambda message: bool(message.document))
async def document_upload(
    message: Message, bot: Bot, session: AsyncSession, settings: Settings
) -> None:
    user = await get_user_by_tg(session, message.from_user.id)
    if not user:
        await message.answer("Сначала выполните /start")
        return
    if user.status == UserStatus.pending:
        await message.answer("Ваш доступ ожидает одобрения администратора.")
        return
    if user.status in {UserStatus.blocked, UserStatus.rejected}:
        await message.answer("Загрузка файлов для вашего аккаунта запрещена.")
        return
    if not user.root_folder:
        await message.answer("Ваша папка ещё не назначена. Обратитесь к администратору.")
        for admin_id in settings.telegram_admin_ids:
            await bot.send_message(
                admin_id,
                f"У активного пользователя {user.telegram_id} не назначена папка Яндекс.Диска.",
            )
        return

    document = message.document
    if not validate_size(document.file_size or 0, settings):
        await message.answer("Файл слишком большой.")
        return

    chat_id = getattr(getattr(message, "chat", None), "id", message.from_user.id)
    message_id = getattr(message, "message_id", document.file_id)
    source_event_key = f"telegram-document:{chat_id}:{message_id}"
    existing = None
    if hasattr(session, "scalar"):
        existing = await session.scalar(
            select(UploadRequest).where(UploadRequest.source_event_key == source_event_key)
        )
    if existing:
        await message.answer(
            f"Файл уже получен: {existing.request_code} (статус: {existing.status.value})"
        )
        return

    safe = sanitize_filename(document.file_name or "file")
    request_code = await next_request_code(session)
    storage = TempStorage(settings.temp_storage_dir)
    destination = storage.path_for(request_code, safe)
    try:
        await download_file(bot, document.file_id, destination)
        sha256 = storage.sha256(destination)
    except Exception:
        destination.unlink(missing_ok=True)
        await message.answer(
            "Не удалось скачать файл из Telegram. Попробуйте отправить файл ещё раз."
        )
        return
    client = YandexDiskClient(settings.yandex_disk_token)
    try:
        target_folder = await ensure_user_folder_for_current_root(session, user, settings, client)
    except Exception:
        if hasattr(session, "rollback"):
            await session.rollback()
        destination.unlink(missing_ok=True)
        await message.answer(
            "Не удалось подготовить папку на Яндекс.Диске. Попробуйте позже или обратитесь к администратору."
        )
        return
    finally:
        await client.close()
    target_path = join_disk_path(target_folder, safe)

    upload = await create_upload_request(
        session,
        request_code=request_code,
        user_id=user.id,
        telegram_file_id=document.file_id,
        telegram_file_unique_id=document.file_unique_id,
        original_filename=document.file_name or safe,
        safe_filename=safe,
        mime_type=document.mime_type,
        size_bytes=document.file_size or destination.stat().st_size,
        sha256=sha256,
        caption=message.caption,
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
            select(UploadRequest).where(UploadRequest.source_event_key == source_event_key)
        )
        if existing:
            await message.answer(
                f"Файл уже получен: {existing.request_code} (статус: {existing.status.value})"
            )
            return
        raise

    await message.answer(
        f"Файл получен и отправлен администратору на проверку: {upload.request_code}"
    )
