from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.callbacks import UploadModerationCallback, UserModerationCallback
from app.config import Settings
from app.db.models import UploadRequest, User, UserStatus
from app.db.repositories import approve_user, pending_requests
from app.utils.security import ensure_admin_callback, is_admin

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
    if callback_data.action == "approve":
        await approve_user(session, user, callback.from_user.id, settings.yandex_disk_root)
    elif callback_data.action == "reject":
        user.status = UserStatus.rejected
    elif callback_data.action == "block":
        user.status = UserStatus.blocked
    await session.commit()
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
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not ensure_admin_callback(callback, settings):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    request = await session.get(UploadRequest, callback_data.request_id)
    await callback.answer(
        f"Действие {callback_data.action} для {request.request_code if request else 'заявки'}"
    )
