from datetime import UTC, datetime
from pathlib import Path

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.callbacks import UploadModerationCallback, UserModerationCallback
from app.bot.keyboards import (
    folder_selection_keyboard,
    reject_reason_keyboard,
    upload_keyboard,
    user_moderation_keyboard,
)
from app.config import Settings
from app.db.models import AuditLog, UploadRequest, UploadStatus, User, UserStatus
from app.db.repositories import approve_user, pending_requests
from app.services.app_settings import (
    get_yandex_disk_root,
    get_yandex_disk_root_setting,
)
from app.services.audit import write_audit
from app.services.disk_paths import DiskPathValidationError, validate_yandex_disk_root
from app.services.naming import (
    FilenameEditError,
    change_filename_extension,
    change_filename_stem,
    join_disk_path,
    user_folder_for_user,
)
from app.services.upload_queue import UploadQueueError, enqueue_upload_request
from app.services.user_folders import change_yandex_disk_root_for_active_users
from app.services.yandex_disk import YandexDiskClient, YandexDiskError
from app.utils.formatting import (
    format_audit_action,
    format_folder_items,
    format_upload_card,
    format_upload_result,
    format_user_status,
)
from app.utils.security import ensure_admin_callback, is_admin

router = Router()

REJECT_REASONS = {
    "reject_duplicate": "Дубликат",
    "reject_wrong_file": "Неверный файл",
    "reject_bad_quality": "Плохое качество",
    "reject_wrong_folder": "Не та папка",
    "reject_other": "Отклонено администратором",
}


class UploadEditStates(StatesGroup):
    waiting_for_filename_stem = State()
    waiting_for_filename_extension = State()
    waiting_for_custom_reject_reason = State()
    waiting_for_yandex_disk_root = State()


@router.message(Command("admin"))
async def admin_panel(message: Message, settings: Settings) -> None:
    if not is_admin(message.from_user.id, settings):
        return
    await message.answer(
        "Админ-панель:\n"
        "/queue — очередь заявок\n"
        "/users — пользователи\n"
        "/audit — аудит\n"
        "/diskroot — текущая корневая папка Яндекс.Диска\n"
        "/setdiskroot disk:/Telegram Uploads — изменить активную корневую папку для новых загрузок"
    )


@router.message(Command("diskroot"))
async def diskroot(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not is_admin(message.from_user.id, settings):
        return
    current = await get_yandex_disk_root_setting(session, settings)
    source = (
        "значение по умолчанию из .env"
        if current.is_default
        else "настройка, заданная администратором"
    )
    await message.answer(
        "Текущая корневая папка Яндекс.Диска:\n"
        f"{current.value}\n\n"
        f"Источник: {source}\n\n"
        "Корневая папка Яндекс.Диска используется для создания папок пользователей и новых загрузок.\n"
        "После изменения новые загрузки будут идти в папки пользователей внутри новой корневой папки.\n"
        "Если папки пользователя там ещё нет, она будет создана повторно.\n"
        "Старые файлы не переносятся."
    )


async def _save_diskroot_change(
    message: Message, session: AsyncSession, settings: Settings, root: str
) -> None:
    admin_id = message.from_user.id
    try:
        validate_yandex_disk_root(root)
    except DiskPathValidationError as exc:
        await message.answer(f"Некорректный путь Яндекс.Диска: {exc}")
        return
    client = YandexDiskClient(settings.yandex_disk_token)
    try:
        new_root = await change_yandex_disk_root_for_active_users(
            session, settings, client, root, admin_id
        )
        await session.commit()
    except Exception as exc:
        if hasattr(session, "rollback"):
            await session.rollback()
        await message.answer(f"Не удалось создать папку/папки Яндекс.Диска: {exc}")
        return
    finally:
        await client.close()
    await message.answer(
        "Корневая папка Яндекс.Диска обновлена:\n"
        f"{new_root}\n\n"
        "Новые загрузки всех пользователей будут идти в папки внутри неё.\n"
        "Если папки пользователя там ещё нет, она будет создана повторно.\n"
        "Старые файлы не переносятся."
    )


@router.message(Command("setdiskroot"))
async def setdiskroot(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not is_admin(message.from_user.id, settings):
        return
    if not command.args:
        await state.set_state(UploadEditStates.waiting_for_yandex_disk_root)
        await message.answer(
            "Отправьте новую корневую папку Яндекс.Диска, например:\ndisk:/Telegram Uploads"
        )
        return
    await _save_diskroot_change(message, session, settings, command.args)


@router.message(UploadEditStates.waiting_for_yandex_disk_root)
async def setdiskroot_from_state(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    if not is_admin(message.from_user.id, settings):
        await state.clear()
        return
    await _save_diskroot_change(message, session, settings, message.text or "")
    await state.clear()


@router.message(Command("queue"))
async def queue(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not is_admin(message.from_user.id, settings):
        return
    requests = await pending_requests(session, limit=10)
    if not requests:
        await message.answer("Очередь пуста")
        return
    for request in requests:
        user = await session.get(User, request.user_id)
        if user:
            await message.answer(
                format_upload_card(request, user), reply_markup=upload_keyboard(request)
            )
    if len(requests) == 10:
        await message.answer("Показаны первые 10 заявок.")


@router.message(Command("users"))
async def users(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not is_admin(message.from_user.id, settings):
        return
    rows = (await session.scalars(select(User).order_by(User.created_at.desc()).limit(20))).all()
    if not rows:
        await message.answer("Нет пользователей")
        return
    for user in rows:
        username = f"@{user.username}" if user.username else "—"
        text = (
            f"Имя: {user.full_name or '—'}\n"
            f"Username Telegram: {username}\n"
            f"ID Telegram: {user.telegram_id}\n"
            f"Статус: {format_user_status(user.status)}\n"
            f"Папка на Яндекс.Диске: {user.root_folder or '—'}"
        )
        markup = user_moderation_keyboard(user.id) if user.status == UserStatus.pending else None
        await message.answer(text, reply_markup=markup)


@router.message(Command("audit"))
async def audit(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not is_admin(message.from_user.id, settings):
        return
    rows = (
        await session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(20))
    ).all()
    if not rows:
        await message.answer("Журнал аудита пуст")
        return
    lines = []
    for row in rows:
        lines.append(
            f"{row.created_at}: администратор={row.actor_telegram_id}; действие={format_audit_action(row.action)}; "
            f"заявка={row.request_id or '—'}; пользователь={row.user_id or '—'}; "
            f"было={row.old_value or '—'}; стало={row.new_value or '—'}"
        )
    await message.answer("\n\n".join(lines))


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
        if user.status != UserStatus.pending:
            await callback.answer(
                f"Пользователь уже обработан: {user.status.value}",
                show_alert=True,
            )
            return
        disk_root = await get_yandex_disk_root(session, settings)
        folder = user_folder_for_user(disk_root, user)
        client = YandexDiskClient(settings.yandex_disk_token)
        try:
            await approve_user(session, user, callback.from_user.id, disk_root)
            await client.mkdir_recursive(folder)
        except ValueError as exc:
            if hasattr(session, "rollback"):
                await session.rollback()
            await callback.answer(str(exc), show_alert=True)
            return
        except Exception as exc:
            if hasattr(session, "rollback"):
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
    request: UploadRequest,
    user: User,
    action: str = "approve",
) -> None:
    try:
        queued = await enqueue_upload_request(session, request.id, action, callback.from_user.id)
    except UploadQueueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.message.answer(
        format_upload_card(queued, user), reply_markup=upload_keyboard(queued)
    )
    await callback.answer("Заявка поставлена в очередь")


async def _reject_request(
    bot: Bot,
    session: AsyncSession,
    admin_id: int,
    request: UploadRequest,
    user: User,
    reason: str,
) -> None:
    old_status = request.status.value
    request.status = UploadStatus.rejected
    request.rejected_at = datetime.now(UTC)
    request.reject_reason = reason
    await write_audit(
        session,
        actor_telegram_id=admin_id,
        action="upload_reject",
        request_id=request.id,
        user_id=user.id,
        old_value={"status": old_status},
        new_value={"status": request.status.value, "reason": reason},
    )
    await session.commit()
    await _notify_upload_result(bot, admin_id, request, user)


@router.callback_query(UploadModerationCallback.filter())
async def upload_callback(
    callback: CallbackQuery,
    callback_data: UploadModerationCallback,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
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
            except YandexDiskError as exc:
                await callback.message.answer(f"Ошибка Яндекс.Диска: {exc}")
                await callback.answer()
                return
        finally:
            await client.close()
        await callback.message.answer(format_folder_items(request.target_folder, items))
        await callback.answer()
        return

    if action in {"approve", "overwrite", "retry", "copy"}:
        await _run_upload(bot, callback, session, request, user, action=action)
        return

    if action == "reject":
        if request.status in {UploadStatus.uploaded, UploadStatus.rejected}:
            await callback.answer("Эту заявку уже нельзя отклонить", show_alert=True)
            return
        await callback.message.answer(
            f"Выберите причину отклонения для {request.request_code}",
            reply_markup=reject_reason_keyboard(request.id),
        )
        await callback.answer()
        return

    if action in REJECT_REASONS:
        if action == "reject_other":
            await state.set_state(UploadEditStates.waiting_for_custom_reject_reason)
            await state.update_data(request_id=request.id)
            await callback.message.answer(
                f"Отправьте причину отклонения для заявки {request.request_code}"
            )
            await callback.answer()
            return
        await _reject_request(
            bot, session, callback.from_user.id, request, user, REJECT_REASONS[action]
        )
        await callback.answer("Заявка отклонена")
        return

    if action in {"rename", "rename_stem"}:
        if request.status in {
            UploadStatus.approved,
            UploadStatus.uploading,
            UploadStatus.uploaded,
            UploadStatus.rejected,
        }:
            await callback.answer("Эту заявку уже нельзя переименовать", show_alert=True)
            return
        await state.set_state(UploadEditStates.waiting_for_filename_stem)
        await state.update_data(request_id=request.id)
        await callback.message.answer(
            "Введите новое имя файла без расширения. Текущее расширение будет сохранено."
        )
        await callback.answer()
        return

    if action == "rename_extension":
        if request.status in {
            UploadStatus.approved,
            UploadStatus.uploading,
            UploadStatus.uploaded,
            UploadStatus.rejected,
        }:
            await callback.answer("Эту заявку уже нельзя переименовать", show_alert=True)
            return
        await state.set_state(UploadEditStates.waiting_for_filename_extension)
        await state.update_data(request_id=request.id)
        await callback.message.answer("Введите новое расширение файла, например: pdf или .pdf")
        await callback.answer()
        return

    if action == "folder":
        folders = user.allowed_folders or []
        if not folders:
            await callback.answer("Для пользователя нет доступных папок", show_alert=True)
            return
        await callback.message.answer(
            f"Выберите папку для заявки {request.request_code}",
            reply_markup=folder_selection_keyboard(request.id, folders),
        )
        await callback.answer()
        return

    if action.startswith("folder_"):
        try:
            index = int(action.removeprefix("folder_"))
            folder = (user.allowed_folders or [])[index]
        except (ValueError, IndexError):
            await callback.answer("Недопустимая папка", show_alert=True)
            return
        if folder not in (user.allowed_folders or []):
            await callback.answer("Недопустимая папка", show_alert=True)
            return
        old = {"target_folder": request.target_folder, "target_path": request.target_path}
        request.target_folder = folder
        request.target_path = join_disk_path(folder, request.safe_filename)
        await write_audit(
            session,
            actor_telegram_id=callback.from_user.id,
            action="upload_folder_change",
            request_id=request.id,
            user_id=user.id,
            old_value=old,
            new_value={"target_folder": request.target_folder, "target_path": request.target_path},
        )
        await session.commit()
        await callback.message.answer(
            format_upload_card(request, user), reply_markup=upload_keyboard(request)
        )
        await callback.answer("Папка изменена")


@router.message(UploadEditStates.waiting_for_filename_stem)
async def rename_upload(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    if not is_admin(message.from_user.id, settings):
        await state.clear()
        return
    data = await state.get_data()
    request = await session.get(UploadRequest, data.get("request_id"))
    if not request:
        await message.answer("Заявка не найдена")
        await state.clear()
        return
    if request.status in {
        UploadStatus.approved,
        UploadStatus.uploading,
        UploadStatus.uploaded,
        UploadStatus.rejected,
    }:
        await message.answer("Эту заявку уже нельзя переименовать")
        await state.clear()
        return
    user = await session.get(User, request.user_id)
    if not user:
        await message.answer("Пользователь заявки не найден")
        await state.clear()
        return
    old = {"safe_filename": request.safe_filename, "target_path": request.target_path}
    try:
        safe = change_filename_stem(request.safe_filename, message.text or "")
    except FilenameEditError as exc:
        await message.answer(f"Не удалось изменить имя файла: {exc}")
        return
    request.safe_filename = safe
    request.target_path = join_disk_path(request.target_folder, safe)
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id,
        action="upload_filename_stem_change",
        request_id=request.id,
        user_id=request.user_id,
        old_value=old,
        new_value={"safe_filename": request.safe_filename, "target_path": request.target_path},
    )
    await session.commit()
    await state.clear()
    await message.answer(format_upload_card(request, user), reply_markup=upload_keyboard(request))


@router.message(UploadEditStates.waiting_for_custom_reject_reason)
async def custom_reject_reason(
    message: Message, bot: Bot, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    if not is_admin(message.from_user.id, settings):
        await state.clear()
        return
    data = await state.get_data()
    request = await session.get(UploadRequest, data.get("request_id"))
    if not request:
        await message.answer("Заявка не найдена")
        await state.clear()
        return
    user = await session.get(User, request.user_id)
    if not user:
        await message.answer("Пользователь заявки не найден")
        await state.clear()
        return
    reason = (message.text or "Отклонено администратором").strip()[:1000]
    await _reject_request(bot, session, message.from_user.id, request, user, reason)
    await state.clear()
    await message.answer("Заявка отклонена")


@router.message(UploadEditStates.waiting_for_filename_extension)
async def rename_extension_upload(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    if not is_admin(message.from_user.id, settings):
        await state.clear()
        return
    data = await state.get_data()
    request = await session.get(UploadRequest, data.get("request_id"))
    if not request:
        await message.answer("Заявка не найдена")
        await state.clear()
        return
    if request.status in {
        UploadStatus.approved,
        UploadStatus.uploading,
        UploadStatus.uploaded,
        UploadStatus.rejected,
    }:
        await message.answer("Эту заявку уже нельзя переименовать")
        await state.clear()
        return
    user = await session.get(User, request.user_id)
    if not user:
        await message.answer("Пользователь заявки не найден")
        await state.clear()
        return
    old = {"safe_filename": request.safe_filename, "target_path": request.target_path}
    try:
        safe = change_filename_extension(request.safe_filename, message.text or "")
    except FilenameEditError as exc:
        await message.answer(f"Не удалось изменить расширение файла: {exc}")
        return
    request.safe_filename = safe
    request.target_path = join_disk_path(request.target_folder, safe)
    await write_audit(
        session,
        actor_telegram_id=message.from_user.id,
        action="upload_filename_extension_change",
        request_id=request.id,
        user_id=request.user_id,
        old_value=old,
        new_value={"safe_filename": request.safe_filename, "target_path": request.target_path},
    )
    await session.commit()
    await state.clear()
    await message.answer(format_upload_card(request, user), reply_markup=upload_keyboard(request))
