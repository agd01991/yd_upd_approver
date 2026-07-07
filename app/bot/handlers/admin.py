from datetime import UTC, datetime
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.callbacks import UploadModerationCallback, UserModerationCallback
from app.bot.keyboards import upload_keyboard
from app.config import Settings
from app.db.models import UploadRequest, UploadStatus, User, UserStatus
from app.db.repositories import approve_user, pending_requests
from app.services.audit import write_audit
from app.services.naming import sanitize_filename
from app.services.yandex_disk import YandexDiskClient
from app.utils.formatting import format_folder_items, format_upload_result
from app.utils.security import ensure_admin_callback, is_admin
from app.workers.upload_worker import upload_approved_request

router = Router()


@router.message(Command("admin"))
async def admin_panel(message: Message, settings: Settings) -> None:
    if not is_admin(message.from_user.id, settings):
        return
    await message.answer("Админ-панель: /queue, /users, /audit")


@router.message(Command("queue"))
async def queue(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not is_admin(message.from_user.id, settings):
        return
    requests = await pending_requests(session)
    await message.answer(
        "\n".join(f"{r.request_code}: {r.safe_filename}" for r in requests) or "Очередь пуста"
    )


@router.message(Command("users"))
async def users(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not is_admin(message.from_user.id, settings):
        return
    rows = (await session.scalars(select(User).order_by(User.created_at.desc()).limit(20))).all()
    await message.answer(
        "\n".join(f"{u.telegram_id}: {u.status.value}" for u in rows) or "Нет пользователей"
    )


@router.callback_query(UserModerationCallback.filter())
async def user_callback(
    callback: CallbackQuery,
    callback_data: UserModerationCallback,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not ensure_admin_callback(callback, settings):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    user = await session.get(User, callback_data.user_id)
    if not user:
        await callback.answer("Пользователь не найден")
        return
    old_status = user.status.value
    if callback_data.action == "approve":
        await approve_user(session, user, callback.from_user.id, settings.yandex_disk_root)
        client = YandexDiskClient(settings.yandex_disk_token)
        try:
            await client.mkdir_recursive(user.root_folder)
        except Exception as exc:
            await session.rollback()
            await callback.answer(f"Не удалось создать папку Яндекс.Диска: {exc}", show_alert=True)
            return
        finally:
            await client.close()
        await bot.send_message(user.telegram_id, "Ваш доступ одобрен. Можете отправлять файлы.")
    elif callback_data.action == "reject":
        user.status = UserStatus.rejected
        await bot.send_message(user.telegram_id, "Ваша заявка на доступ отклонена.")
    elif callback_data.action == "block":
        user.status = UserStatus.blocked
        await bot.send_message(user.telegram_id, "Ваш доступ заблокирован администратором.")
    await write_audit(
        session,
        actor_telegram_id=callback.from_user.id,
        action=f"user_{callback_data.action}",
        user_id=user.id,
        old_value={"status": old_status},
        new_value={"status": user.status.value, "root_folder": user.root_folder},
    )
    await session.commit()
    await callback.answer("Готово")


async def _notify_upload_result(bot: Bot, admin_id: int, upload: UploadRequest, user: User) -> None:
    text = format_upload_result(upload)
    await bot.send_message(admin_id, text)
    if upload.status == UploadStatus.uploaded:
        await bot.send_message(user.telegram_id, f"Ваш файл загружен: {upload.request_code}")
    elif upload.status == UploadStatus.failed:
        await bot.send_message(
            user.telegram_id,
            f"Загрузка файла {upload.request_code} временно не удалась. Администратор может повторить.",
        )
    elif upload.status == UploadStatus.rejected:
        await bot.send_message(user.telegram_id, text)


async def _run_upload(
    bot: Bot,
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    request: UploadRequest,
    user: User,
    overwrite: bool = False,
) -> None:
    if request.status not in {
        UploadStatus.pending_approval,
        UploadStatus.failed,
        UploadStatus.approved,
    }:
        await callback.answer(
            f"Нельзя загрузить заявку в статусе {request.status.value}", show_alert=True
        )
        return
    request.status = UploadStatus.approved
    request.approved_at = datetime.now(UTC)
    request.approved_by = callback.from_user.id
    client = YandexDiskClient(settings.yandex_disk_token)
    try:
        await upload_approved_request(session, request, client, overwrite=overwrite)
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            action="upload_overwrite" if overwrite else "upload_approve",
            request_id=request.id,
            user_id=user.id,
            new_value={"status": request.status.value, "target_path": request.target_path},
        )
        await session.commit()
    finally:
        await client.close()
    await _notify_upload_result(bot, callback.from_user.id, request, user)
    if request.status == UploadStatus.failed:
        await bot.send_message(
            callback.from_user.id,
            "Если это конфликт имени, выберите: загрузить как копию, перезаписать или повторить.",
            reply_markup=upload_keyboard(request.id),
        )
    await callback.answer("Готово")


@router.callback_query(
    UploadModerationCallback.filter(
        F.action.in_(
            {"open", "approve", "reject", "rename", "folder", "list", "copy", "overwrite", "retry"}
        )
    )
)
async def upload_callback(
    callback: CallbackQuery,
    callback_data: UploadModerationCallback,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not ensure_admin_callback(callback, settings):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    request = await session.get(UploadRequest, callback_data.request_id)
    if not request:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    user = await session.get(User, request.user_id)
    if not user:
        await callback.answer("Пользователь заявки не найден", show_alert=True)
        return

    action = callback_data.action
    if action == "open":
        if not request.local_path or not Path(request.local_path).exists():
            await callback.message.answer(
                "Временный файл не найден. Загрузку нужно повторить пользователем."
            )
            await callback.answer()
            return
        await callback.message.answer_document(
            FSInputFile(request.local_path, filename=request.safe_filename)
        )
        await callback.answer()
        return

    if action == "list":
        client = YandexDiskClient(settings.yandex_disk_token)
        try:
            try:
                items = await client.list_files(request.target_folder)
            except FileNotFoundError:
                await callback.message.answer("Папка ещё не создана")
                await callback.answer()
                return
        finally:
            await client.close()
        await callback.message.answer(format_folder_items(request.target_folder, items))
        await callback.answer()
        return

    if action in {"approve", "overwrite", "retry"}:
        if action == "retry" and request.status != UploadStatus.failed:
            await callback.answer("Повторить можно только заявку в статусе failed", show_alert=True)
            return
        await _run_upload(
            bot, callback, session, settings, request, user, overwrite=action == "overwrite"
        )
        return

    if action == "copy":
        client = YandexDiskClient(settings.yandex_disk_token)
        try:
            old_target_path = request.target_path
            request.target_path = await client.resolve_conflict_copy(
                request.target_folder, request.safe_filename, request.request_code
            )
            await write_audit(
                session,
                actor_telegram_id=callback.from_user.id,
                action="upload_copy_path",
                request_id=request.id,
                user_id=user.id,
                old_value={"target_path": old_target_path},
                new_value={"target_path": request.target_path},
            )
        finally:
            await client.close()
        await _run_upload(bot, callback, session, settings, request, user, overwrite=False)
        return

    if action == "reject":
        request.status = UploadStatus.rejected
        request.rejected_at = datetime.now(UTC)
        request.reject_reason = "Отклонено администратором"
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            action="upload_reject",
            request_id=request.id,
            user_id=user.id,
            new_value={"status": request.status.value, "reason": request.reject_reason},
        )
        await session.commit()
        await _notify_upload_result(bot, callback.from_user.id, request, user)
        await callback.answer("Заявка отклонена")
        return

    if action == "rename":
        safe = sanitize_filename(request.safe_filename)
        await callback.message.answer(
            f"Переименование будет добавлено следующим этапом. Текущее безопасное имя: {safe}"
        )
        await callback.answer()
        return

    if action == "folder":
        await callback.message.answer("Смена папки будет добавлена следующим этапом.")
        await callback.answer()
