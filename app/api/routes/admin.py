from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_user_dep, bot_dep, get_db, settings_dep
from app.api.schemas import AllowedFoldersResponse, RejectBody, UploadPatch
from app.api.security import TelegramWebAppUser
from app.config import Settings
from app.db.models import AuditLog, UploadRequest, UploadStatus, User, UserStatus
from app.db.repositories import approve_user
from app.services.app_settings import get_yandex_disk_root
from app.services.audit import write_audit
from app.services.file_policy import folder_allowed
from app.services.naming import join_disk_path, sanitize_filename
from app.services.yandex_disk import YandexDiskClient
from app.workers.upload_worker import upload_approved_request

router = APIRouter(prefix="/admin", dependencies=[Depends(admin_user_dep)])


def user_json(u: User) -> dict:
    return {
        "id": u.id,
        "telegram_id": u.telegram_id,
        "username": u.username,
        "full_name": u.full_name,
        "status": u.status.value,
        "root_folder_assigned": bool(u.root_folder),
    }


def req_json(r: UploadRequest, user: User | None = None) -> dict:
    return {
        "id": r.id,
        "request_code": r.request_code,
        "user": user_json(user) if user else None,
        "original_filename": r.original_filename,
        "safe_filename": r.safe_filename,
        "size_bytes": r.size_bytes,
        "status": r.status.value,
        "sha256": r.sha256,
        "caption": r.caption,
        "target_folder": r.target_folder,
        "target_path": r.target_path,
        "error_message": r.error_message,
        "reject_reason": r.reject_reason,
        "created_at": r.created_at,
        "uploaded_at": r.uploaded_at,
    }


def _folder_label(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1] or path


@router.get("/users")
async def users(session: AsyncSession = Depends(get_db)) -> list[dict]:
    return [
        user_json(u)
        for u in (
            await session.scalars(select(User).order_by(User.created_at.desc()).limit(100))
        ).all()
    ]


async def _moderate_user(
    user_id: int, action: str, actor: int, session: AsyncSession, settings: Settings, bot
) -> dict:
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    old = user.status.value
    if action == "approve":
        if user.status != UserStatus.pending:
            raise HTTPException(400, "User is already processed")
        disk_root = await get_yandex_disk_root(session, settings)
        await approve_user(session, user, actor, disk_root)
        client = YandexDiskClient(settings.yandex_disk_token)
        try:
            await client.mkdir_recursive(user.root_folder)
        finally:
            await client.close()
        msg = "Ваш доступ одобрен. Можете отправлять файлы."
    elif action == "reject":
        user.status = UserStatus.rejected
        msg = "Ваша заявка на доступ отклонена."
    else:
        user.status = UserStatus.blocked
        msg = "Ваш доступ заблокирован администратором."
    await write_audit(
        session,
        actor,
        f"user_{action}",
        user_id=user.id,
        old_value={"status": old},
        new_value={"status": user.status.value, "root_folder": user.root_folder},
    )
    await session.commit()
    if bot:
        await bot.send_message(user.telegram_id, msg)
    return user_json(user)


@router.post("/users/{user_id}/{action}")
async def moderate_user(
    user_id: int,
    action: str,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    bot=Depends(bot_dep),
) -> dict:
    if action not in {"approve", "reject", "block"}:
        raise HTTPException(404, "Unknown action")
    return await _moderate_user(user_id, action, actor.telegram_id, session, settings, bot)


@router.get("/uploads")
async def uploads(session: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (
        await session.scalars(
            select(UploadRequest).order_by(UploadRequest.created_at.desc()).limit(100)
        )
    ).all()
    return [req_json(r, await session.get(User, r.user_id)) for r in rows]


@router.get("/uploads/{request_id}")
async def upload_detail(request_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    r = await session.get(UploadRequest, request_id)
    if not r:
        raise HTTPException(404, "Request not found")
    return req_json(r, await session.get(User, r.user_id))


@router.get("/uploads/{request_id}/download-temp")
async def download_temp(request_id: int, session: AsyncSession = Depends(get_db)):
    r = await session.get(UploadRequest, request_id)
    if not r or not r.local_path or not Path(r.local_path).exists():
        raise HTTPException(404, "Temp file not found")
    return FileResponse(r.local_path, filename=r.safe_filename)


@router.get("/uploads/{request_id}/allowed-folders", response_model=AllowedFoldersResponse)
async def allowed_folders(request_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    r = await session.get(UploadRequest, request_id)
    user = await session.get(User, r.user_id) if r else None
    if not r or not user:
        raise HTTPException(404, "Request not found")
    return {
        "items": [{"path": path, "label": _folder_label(path)} for path in user.allowed_folders]
    }


@router.get("/uploads/{request_id}/folder-items")
async def folder_items(
    request_id: int,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    r = await session.get(UploadRequest, request_id)
    if not r:
        raise HTTPException(404, "Request not found")
    client = YandexDiskClient(settings.yandex_disk_token)
    try:
        items = await client.list_files(r.target_folder)
    except FileNotFoundError:
        items = []
    finally:
        await client.close()
    return {
        "items": [
            {"name": i.get("name"), "type": i.get("type"), "size": i.get("size")} for i in items
        ]
    }


async def _run_action(
    request_id: int, action: str, actor: int, session: AsyncSession, settings: Settings, bot
) -> dict:
    r = await session.get(UploadRequest, request_id)
    user = await session.get(User, r.user_id) if r else None
    if not r or not user:
        raise HTTPException(404, "Request not found")
    if action == "retry" and r.status != UploadStatus.failed:
        raise HTTPException(400, "Retry only failed requests")
    if action in {"approve", "overwrite", "retry", "copy"}:
        if r.status not in {
            UploadStatus.pending_approval,
            UploadStatus.failed,
            UploadStatus.approved,
        }:
            raise HTTPException(400, "Invalid status")
        if action == "copy":
            client = YandexDiskClient(settings.yandex_disk_token)
            try:
                r.target_path = await client.resolve_conflict_copy(
                    r.target_folder, r.safe_filename, r.request_code
                )
            finally:
                await client.close()
        r.status = UploadStatus.approved
        r.approved_at = datetime.now(UTC)
        r.approved_by = actor
        client = YandexDiskClient(settings.yandex_disk_token)
        try:
            await upload_approved_request(session, r, client, overwrite=action == "overwrite")
        finally:
            await client.close()
        await write_audit(
            session,
            actor,
            f"upload_{action}",
            request_id=r.id,
            user_id=user.id,
            new_value={"status": r.status.value, "target_path": r.target_path},
        )
        await session.commit()
        if bot:
            await bot.send_message(
                user.telegram_id, f"Статус заявки {r.request_code}: {r.status.value}"
            )
        return req_json(r, user)
    raise HTTPException(404, "Unknown action")


async def upload_action(
    request_id: int,
    action: str,
    actor: TelegramWebAppUser,
    session: AsyncSession,
    settings: Settings,
    bot,
) -> dict:
    return await _run_action(request_id, action, actor.telegram_id, session, settings, bot)


@router.post("/uploads/{request_id}/approve")
async def upload_approve(
    request_id: int,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    bot=Depends(bot_dep),
) -> dict:
    return await upload_action(request_id, "approve", actor, session, settings, bot)


@router.post("/uploads/{request_id}/copy")
async def upload_copy(
    request_id: int,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    bot=Depends(bot_dep),
) -> dict:
    return await upload_action(request_id, "copy", actor, session, settings, bot)


@router.post("/uploads/{request_id}/overwrite")
async def upload_overwrite(
    request_id: int,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    bot=Depends(bot_dep),
) -> dict:
    return await upload_action(request_id, "overwrite", actor, session, settings, bot)


@router.post("/uploads/{request_id}/retry")
async def upload_retry(
    request_id: int,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    bot=Depends(bot_dep),
) -> dict:
    return await upload_action(request_id, "retry", actor, session, settings, bot)


@router.post("/uploads/{request_id}/reject")
async def reject_upload(
    request_id: int,
    body: RejectBody,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
    bot=Depends(bot_dep),
) -> dict:
    r = await session.get(UploadRequest, request_id)
    user = await session.get(User, r.user_id) if r else None
    if not r or not user:
        raise HTTPException(404, "Request not found")
    if r.status in {UploadStatus.uploaded, UploadStatus.rejected}:
        raise HTTPException(400, "Cannot reject")
    old = r.status.value
    r.status = UploadStatus.rejected
    r.rejected_at = datetime.now(UTC)
    r.reject_reason = body.reason[:1000]
    await write_audit(
        session,
        actor.telegram_id,
        "upload_reject",
        request_id=r.id,
        user_id=user.id,
        old_value={"status": old},
        new_value={"status": r.status.value, "reason": r.reject_reason},
    )
    await session.commit()
    if bot:
        await bot.send_message(
            user.telegram_id, f"Заявка {r.request_code} отклонена: {r.reject_reason}"
        )
    return req_json(r, user)


@router.patch("/uploads/{request_id}")
async def patch_upload(
    request_id: int,
    body: UploadPatch,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
) -> dict:
    r = await session.get(UploadRequest, request_id)
    user = await session.get(User, r.user_id) if r else None
    if not r or not user:
        raise HTTPException(404, "Request not found")
    if r.status in {UploadStatus.uploaded, UploadStatus.rejected}:
        raise HTTPException(400, "Cannot edit")
    old = {
        "safe_filename": r.safe_filename,
        "target_folder": r.target_folder,
        "target_path": r.target_path,
    }
    if body.target_folder:
        if not folder_allowed(user, body.target_folder):
            raise HTTPException(400, "Folder is not allowed")
        r.target_folder = body.target_folder
    if body.safe_filename:
        r.safe_filename = sanitize_filename(body.safe_filename)
    r.target_path = join_disk_path(r.target_folder, r.safe_filename)
    await write_audit(
        session,
        actor.telegram_id,
        "upload_patch",
        request_id=r.id,
        user_id=user.id,
        old_value=old,
        new_value={
            "safe_filename": r.safe_filename,
            "target_folder": r.target_folder,
            "target_path": r.target_path,
        },
    )
    await session.commit()
    return req_json(r, user)


@router.get("/audit")
async def audit(session: AsyncSession = Depends(get_db), limit: int = 50) -> list[dict]:
    rows = (
        await session.scalars(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(limit, 100))
        )
    ).all()
    return [
        {
            "id": a.id,
            "actor_telegram_id": a.actor_telegram_id,
            "action": a.action,
            "request_id": a.request_id,
            "user_id": a.user_id,
            "old_value": a.old_value,
            "new_value": a.new_value,
            "created_at": a.created_at,
        }
        for a in rows
    ]
