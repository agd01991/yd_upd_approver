from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import bot_dep, current_user_dep, get_db, settings_dep
from app.bot.keyboards import user_moderation_keyboard
from app.config import Settings
from app.db.models import User
from app.utils.formatting import format_user_card

router = APIRouter()


def user_json(user: User, is_admin: bool) -> dict:
    return {
        "telegram_id": user.telegram_id,
        "username": user.username,
        "full_name": user.full_name,
        "status": user.status.value,
        "is_admin": is_admin,
        "root_folder_assigned": bool(user.root_folder),
        "root_folder_label": user.root_folder
        if is_admin
        else (user.root_folder.rsplit("/", 2)[-2] if user.root_folder else None),
    }


@router.get("/me")
async def me(
    current: tuple[User, bool] = Depends(current_user_dep),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    bot=Depends(bot_dep),
) -> dict:
    user, created = current
    if created and bot:
        for admin_id in settings.telegram_admin_ids:
            await bot.send_message(
                admin_id, format_user_card(user), reply_markup=user_moderation_keyboard(user.id)
            )
    await session.commit()
    return user_json(user, user.telegram_id in settings.telegram_admin_ids)
