from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import bot_dep, current_user_dep, get_db, settings_dep
from app.api.errors import ApiError
from app.api.schemas import FolderProfileBody, FolderRenameRequestCreate
from app.bot.keyboards import user_moderation_keyboard
from app.config import Settings
from app.db.models import FolderRenameRequest, FolderRenameRequestStatus, User, UserStatus
from app.services.naming import (
    FolderNameValidationError,
    build_recommended_user_folder_name,
    validate_user_folder_name,
)
from app.utils.formatting import format_folder_rename_request, format_user_card

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
        "folder_name": user.folder_name,
        "contract_number": user.contract_number,
        "contract_date": user.contract_date,
        "contract_full_name": user.contract_full_name,
    }


@router.get("/me")
async def me(
    current: tuple[User, bool] = Depends(current_user_dep),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    bot=Depends(bot_dep),
) -> dict:
    user, created = current
    if created and bot and user.folder_name:
        for admin_id in settings.telegram_admin_ids:
            await bot.send_message(
                admin_id, format_user_card(user), reply_markup=user_moderation_keyboard(user.id)
            )
    await session.commit()
    return user_json(user, user.telegram_id in settings.telegram_admin_ids)


async def _notify_admins(bot, settings: Settings, user: User) -> None:
    if not bot:
        return
    for admin_id in settings.telegram_admin_ids:
        await bot.send_message(
            admin_id, format_user_card(user), reply_markup=user_moderation_keyboard(user.id)
        )


@router.post("/me/folder-profile")
async def save_folder_profile(
    body: FolderProfileBody,
    current: tuple[User, bool] = Depends(current_user_dep),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    bot=Depends(bot_dep),
) -> dict:
    user, _ = current
    try:
        folder_name = validate_user_folder_name(
            body.requested_folder_name
            or build_recommended_user_folder_name(
                body.contract_number, body.contract_date, body.contract_full_name
            )
        )
    except FolderNameValidationError as exc:
        raise ApiError(400, "invalid_request", "Проверьте корректность введённых данных.") from exc
    was_missing = not user.folder_name
    user.contract_number = body.contract_number.strip()
    user.contract_date = body.contract_date.strip()
    user.contract_full_name = body.contract_full_name.strip()
    user.folder_name = folder_name
    await session.commit()
    if was_missing:
        await _notify_admins(bot, settings, user)
    return {"status": user.status.value, "folder_name": user.folder_name}


@router.post("/me/folder-rename-requests")
async def create_my_folder_rename_request(
    body: FolderRenameRequestCreate,
    current: tuple[User, bool] = Depends(current_user_dep),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    bot=Depends(bot_dep),
) -> dict:
    user, _ = current
    if user.status != UserStatus.active:
        raise ApiError(
            400,
            "invalid_request_state",
            "Заявка на переименование доступна только активному пользователю.",
        )
    existing = await session.scalar(
        select(FolderRenameRequest).where(
            FolderRenameRequest.user_id == user.id,
            FolderRenameRequest.status == FolderRenameRequestStatus.pending,
        )
    )
    if existing:
        return {"id": existing.id, "status": existing.status.value}
    try:
        folder_name = validate_user_folder_name(body.requested_folder_name)
    except FolderNameValidationError as exc:
        raise ApiError(400, "invalid_request", "Проверьте корректность введённых данных.") from exc
    req = FolderRenameRequest(
        user_id=user.id,
        requested_folder_name=folder_name,
        contract_number=body.contract_number.strip(),
        contract_date=body.contract_date.strip(),
        contract_full_name=body.contract_full_name.strip(),
    )
    session.add(req)
    await session.flush()
    await session.commit()
    if bot:
        for admin_id in settings.telegram_admin_ids:
            await bot.send_message(
                admin_id,
                format_folder_rename_request(req, user)
                + "\nОткройте Mini App → Заявки на переименование.",
            )
    return {"id": req.id, "status": req.status.value}


@router.get("/me/folder-rename-requests")
async def my_folder_rename_requests(
    current: tuple[User, bool] = Depends(current_user_dep), session: AsyncSession = Depends(get_db)
) -> dict:
    user, _ = current
    rows = (
        await session.scalars(
            select(FolderRenameRequest)
            .where(FolderRenameRequest.user_id == user.id)
            .order_by(FolderRenameRequest.created_at.desc())
            .limit(20)
        )
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "requested_folder_name": r.requested_folder_name,
                "status": r.status.value,
                "reject_reason": r.reject_reason,
                "target_folder": r.target_folder,
            }
            for r in rows
        ]
    }
