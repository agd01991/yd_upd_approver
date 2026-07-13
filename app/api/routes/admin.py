from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_user_dep, get_db, settings_dep
from app.api.errors import ApiError
from app.api.schemas import (
    AdminRenameFolderBody,
    AllowedFoldersResponse,
    DiskRootUpdate,
    FolderRenameApproveBody,
    FolderRenameRejectBody,
    RejectBody,
    UploadPatch,
)
from app.api.security import TelegramWebAppUser
from app.config import Settings
from app.db.models import (
    AuditLog,
    FolderRenameRequest,
    FolderRenameRequestStatus,
    UploadRequest,
    UploadStatus,
    User,
    UserStatus,
)
from app.db.repositories import approve_user
from app.services.app_settings import get_yandex_disk_root, get_yandex_disk_root_setting
from app.services.audit import write_audit
from app.services.file_policy import folder_allowed
from app.services.naming import (
    FilenameEditError,
    change_filename_extension,
    change_filename_stem,
    join_disk_path,
    sanitize_filename,
    user_folder_for_user,
)
from app.services.telegram_outbox import TelegramEventType, enqueue_telegram_event
from app.services.upload_queue import (
    EDITABLE_UPLOAD_STATUSES,
    UploadQueueError,
    enqueue_upload_request,
    reject_upload_request,
)
from app.services.user_folders import change_yandex_disk_root_for_active_users
from app.services.yandex_disk import YandexDiskClient

router = APIRouter(prefix="/admin", dependencies=[Depends(admin_user_dep)])


def _root_folder_label(path: str | None) -> str | None:
    if not path:
        return None
    return path.rstrip("/").rsplit("/", 1)[-1] or None


def user_json(u: User) -> dict:
    return {
        "id": u.id,
        "telegram_id": u.telegram_id,
        "username": u.username,
        "full_name": u.full_name,
        "status": u.status.value,
        "root_folder_assigned": bool(u.root_folder),
        "root_folder_label": _root_folder_label(u.root_folder),
        "folder_name": getattr(u, "folder_name", None),
        "contract_number": getattr(u, "contract_number", None),
        "contract_date": getattr(u, "contract_date", None),
        "contract_full_name": getattr(u, "contract_full_name", None),
    }


def rename_request_json(r: FolderRenameRequest, user: User | None = None) -> dict:
    return {
        "id": r.id,
        "user": user_json(user) if user else None,
        "user_id": r.user_id,
        "requested_folder_name": r.requested_folder_name,
        "contract_number": r.contract_number,
        "contract_date": r.contract_date,
        "contract_full_name": r.contract_full_name,
        "status": r.status.value,
        "source_folder": r.source_folder,
        "target_folder": r.target_folder,
        "reject_reason": r.reject_reason,
        "created_at": r.created_at,
        "reviewed_at": r.reviewed_at,
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


@router.get("/users/search")
async def search_users(query: str = "", session: AsyncSession = Depends(get_db)) -> dict:
    q = query.strip()
    if not q:
        return {"items": []}
    like = f"%{q}%"
    conditions = [
        User.username.ilike(like),
        User.full_name.ilike(like),
        User.contract_full_name.ilike(like),
        User.contract_number.ilike(like),
        User.folder_name.ilike(like),
    ]
    if q.isdigit():
        conditions.append(User.telegram_id == int(q))
    rows = (
        await session.scalars(
            select(User).where(or_(*conditions)).order_by(User.created_at.desc()).limit(20)
        )
    ).all()
    return {"items": [user_json(u) for u in rows]}


@router.get("/users/{user_id}/folder-candidates")
async def folder_candidates(
    user_id: int,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    from app.services.user_folder_rename import get_user_folder_candidates

    user = await session.get(User, user_id)
    if not user:
        raise ApiError(404, "user_not_found", "Пользователь не найден.")
    items = await get_user_folder_candidates(session, user, settings)
    return {"items": [c.__dict__ for c in items]}


@router.post("/users/{user_id}/rename-folder")
async def admin_rename_folder(
    user_id: int,
    body: AdminRenameFolderBody,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    from app.services.user_folder_rename import rename_user_folder

    user = await session.get(User, user_id)
    if not user:
        raise ApiError(404, "user_not_found", "Пользователь не найден.")
    client = YandexDiskClient(settings.yandex_disk_token)
    try:
        target = await rename_user_folder(
            session, user, body.source_folder, body.new_folder_name, actor.telegram_id, client
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        raise ApiError(400, "invalid_request", "Проверьте корректность введённых данных.") from exc
    except Exception as exc:
        await session.rollback()
        raise ApiError(
            503, "yandex_disk_unavailable", "Не удалось переименовать папку Яндекс.Диска"
        ) from exc
    finally:
        await client.close()
    return {"user": user_json(user), "target_folder": target}


async def _moderate_user(
    user_id: int, action: str, actor: int, session: AsyncSession, settings: Settings, bot=None
) -> dict:
    user = await session.get(User, user_id)
    if not user:
        raise ApiError(404, "user_not_found", "Пользователь не найден.")
    old = user.status.value
    if action == "approve":
        if user.status != UserStatus.pending:
            raise ApiError(400, "invalid_request_state", "Пользователь уже обработан.")
        disk_root = await get_yandex_disk_root(session, settings)
        folder = user_folder_for_user(disk_root, user)
        client = YandexDiskClient(settings.yandex_disk_token)
        try:
            await approve_user(session, user, actor, disk_root)
            await client.mkdir_recursive(folder)
        except ValueError as exc:
            if hasattr(session, "rollback"):
                await session.rollback()
            raise ApiError(
                400, "invalid_request", "Проверьте корректность введённых данных."
            ) from exc
        except Exception as exc:
            if hasattr(session, "rollback"):
                await session.rollback()
            raise ApiError(
                503, "yandex_disk_unavailable", "Не удалось создать папку Яндекс.Диска"
            ) from exc
        finally:
            await client.close()
    elif action == "reject":
        user.status = UserStatus.rejected
    else:
        user.status = UserStatus.blocked
    await write_audit(
        session,
        actor,
        f"user_{action}",
        user_id=user.id,
        old_value={"status": old},
        new_value={"status": user.status.value, "root_folder": user.root_folder},
    )
    await enqueue_telegram_event(
        session,
        event_type=TelegramEventType.user_moderation_result,
        recipient_telegram_id=user.telegram_id,
        dedup_key=f"user:{user.id}:moderation:{user.status.value}",
        payload={"status": user.status.value},
        user_id=user.id,
    )
    await session.commit()
    return user_json(user)


@router.post("/users/{user_id}/{action}")
async def moderate_user(
    user_id: int,
    action: str,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    bot=None,
) -> dict:
    if action not in {"approve", "reject", "block"}:
        raise ApiError(404, "not_found", "Unknown action")
    return await _moderate_user(user_id, action, actor.telegram_id, session, settings, bot)


@router.get("/disk-root")
async def get_disk_root(
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    current = await get_yandex_disk_root_setting(session, settings)
    return {
        "value": current.value,
        "is_default": current.is_default,
        "source": "env" if current.is_default else "database",
    }


@router.put("/disk-root")
async def put_disk_root(
    body: DiskRootUpdate,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    client = YandexDiskClient(settings.yandex_disk_token)
    try:
        root = await change_yandex_disk_root_for_active_users(
            session, settings, client, body.root, actor.telegram_id
        )
        await session.commit()
    except Exception as exc:
        if hasattr(session, "rollback"):
            await session.rollback()
        raise ApiError(
            503, "yandex_disk_unavailable", "Не удалось сохранить корневую папку Яндекс.Диска"
        ) from exc
    finally:
        await client.close()
    return {"value": root, "is_default": False, "source": "database"}


def _parse_upload_status(status: str | None) -> UploadStatus | None:
    if not status or status == "all":
        return None
    try:
        return UploadStatus(status)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", "Неизвестный статус заявки") from exc


def _user_query_filter(user_query: str | None):
    if not user_query or not user_query.strip():
        return None
    query = user_query.strip()
    conditions = [User.username.ilike(f"%{query}%"), User.full_name.ilike(f"%{query}%")]
    if query.isdigit():
        conditions.append(User.telegram_id == int(query))
    return or_(*conditions)


@router.get("/uploads")
async def uploads(
    status: str | None = None,
    user_query: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> list[dict]:
    upload_status = _parse_upload_status(status)
    stmt = select(UploadRequest, User).join(User, UploadRequest.user_id == User.id)
    if upload_status:
        stmt = stmt.where(UploadRequest.status == upload_status)
    user_filter = _user_query_filter(user_query)
    if user_filter is not None:
        stmt = stmt.where(user_filter)
    rows = (await session.execute(stmt.order_by(UploadRequest.created_at.desc()).limit(100))).all()
    return [req_json(upload, user) for upload, user in rows]


@router.get("/uploads/{request_id}")
async def upload_detail(request_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    r = await session.get(UploadRequest, request_id)
    if not r:
        raise ApiError(404, "request_not_found", "Заявка не найдена.")
    return req_json(r, await session.get(User, r.user_id))


@router.get("/uploads/{request_id}/download-temp")
async def download_temp(request_id: int, session: AsyncSession = Depends(get_db)):
    r = await session.get(UploadRequest, request_id)
    if not r or not r.local_path or not Path(r.local_path).exists():
        raise ApiError(404, "request_not_found", "Временный файл не найден.")
    return FileResponse(r.local_path, filename=r.safe_filename)


@router.get("/uploads/{request_id}/allowed-folders", response_model=AllowedFoldersResponse)
async def allowed_folders(request_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    r = await session.get(UploadRequest, request_id)
    user = await session.get(User, r.user_id) if r else None
    if not r or not user:
        raise ApiError(404, "request_not_found", "Заявка не найдена.")
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
        raise ApiError(404, "request_not_found", "Заявка не найдена.")
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


async def _run_action(request_id: int, action: str, actor: int, session: AsyncSession) -> dict:
    try:
        r = await enqueue_upload_request(session, request_id, action, actor)
    except UploadQueueError as exc:
        status_code = 404 if exc.code in {"request_not_found", "not_found"} else 400
        raise ApiError(status_code, exc.code, str(exc)) from exc
    user = await session.get(User, r.user_id)
    return req_json(r, user)


async def upload_action(
    request_id: int,
    action: str,
    actor: TelegramWebAppUser,
    session: AsyncSession,
) -> dict:
    return await _run_action(request_id, action, actor.telegram_id, session)


@router.post("/uploads/{request_id}/approve", status_code=202)
async def upload_approve(
    request_id: int,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
) -> dict:
    return await upload_action(request_id, "approve", actor, session)


@router.post("/uploads/{request_id}/copy", status_code=202)
async def upload_copy(
    request_id: int,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
) -> dict:
    return await upload_action(request_id, "copy", actor, session)


@router.post("/uploads/{request_id}/overwrite", status_code=202)
async def upload_overwrite(
    request_id: int,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
) -> dict:
    return await upload_action(request_id, "overwrite", actor, session)


@router.post("/uploads/{request_id}/retry", status_code=202)
async def upload_retry(
    request_id: int,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
) -> dict:
    return await upload_action(request_id, "retry", actor, session)


@router.post("/uploads/{request_id}/reject")
async def reject_upload(
    request_id: int,
    body: RejectBody,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
) -> dict:
    try:
        r = await reject_upload_request(session, request_id, actor.telegram_id, body.reason)
    except UploadQueueError as exc:
        if exc.code == "request_not_found":
            raise ApiError(404, exc.code, str(exc)) from exc
        raise ApiError(409, exc.code, str(exc)) from exc
    user = await session.get(User, r.user_id)
    return req_json(r, user)


@router.patch("/uploads/{request_id}")
async def patch_upload(
    request_id: int,
    body: UploadPatch,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
) -> dict:
    result = await session.execute(
        select(UploadRequest)
        .where(UploadRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    r = result.scalar_one_or_none()
    user = await session.get(User, r.user_id, populate_existing=True) if r else None
    if not r or not user:
        raise ApiError(404, "request_not_found", "Заявка не найдена.")
    if r.status not in EDITABLE_UPLOAD_STATUSES:
        raise ApiError(400, "invalid_request_state", "Заявку нельзя изменить в текущем состоянии.")
    old = {
        "safe_filename": r.safe_filename,
        "target_folder": r.target_folder,
        "target_path": r.target_path,
    }
    filename_fields = [
        name
        for name, value in {
            "safe_filename": body.safe_filename,
            "filename_stem": body.filename_stem,
            "filename_extension": body.filename_extension,
        }.items()
        if value is not None
    ]
    if len(filename_fields) > 1:
        raise ApiError(
            400, "invalid_request", "Передайте только одно поле изменения имени или расширения."
        )
    if body.target_folder:
        if not folder_allowed(user, body.target_folder):
            raise ApiError(400, "folder_not_allowed", "Папка недоступна пользователю.")
        r.target_folder = body.target_folder
    try:
        if body.filename_stem is not None:
            r.safe_filename = change_filename_stem(r.safe_filename, body.filename_stem)
        elif body.filename_extension is not None:
            r.safe_filename = change_filename_extension(r.safe_filename, body.filename_extension)
        elif body.safe_filename is not None:
            r.safe_filename = sanitize_filename(body.safe_filename)
    except FilenameEditError as exc:
        raise ApiError(400, "invalid_request", "Проверьте корректность введённых данных.") from exc
    r.target_path = join_disk_path(r.target_folder, r.safe_filename)
    if old == {
        "safe_filename": r.safe_filename,
        "target_folder": r.target_folder,
        "target_path": r.target_path,
    }:
        await session.commit()
        return req_json(r, user)
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


@router.get("/folder-rename-requests")
async def admin_folder_rename_requests(
    status: str = "pending", session: AsyncSession = Depends(get_db)
) -> dict:
    query = select(FolderRenameRequest, User).join(User).order_by(FolderRenameRequest.created_at)
    if status and status != "all":
        try:
            req_status = FolderRenameRequestStatus(status)
        except ValueError as exc:
            raise ApiError(400, "invalid_request", "Неизвестный статус заявки") from exc
        query = query.where(FolderRenameRequest.status == req_status)
    rows = (await session.execute(query)).all()
    return {"items": [rename_request_json(r, u) for r, u in rows]}


@router.post("/folder-rename-requests/{request_id}/approve")
async def approve_folder_rename_request(
    request_id: int,
    body: FolderRenameApproveBody,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    from app.services.user_folder_rename import rename_user_folder

    req = await session.get(FolderRenameRequest, request_id)
    user = await session.get(User, req.user_id) if req else None
    if not req or not user:
        raise ApiError(404, "request_not_found", "Заявка не найдена.")
    if req.status != FolderRenameRequestStatus.pending:
        raise ApiError(409, "invalid_request_state", "Заявка уже обработана.")
    client = YandexDiskClient(settings.yandex_disk_token)
    try:
        target = await rename_user_folder(
            session, user, body.source_folder, req.requested_folder_name, actor.telegram_id, client
        )
        req.status = FolderRenameRequestStatus.approved
        req.source_folder = body.source_folder
        req.target_folder = target
        req.reviewed_at = datetime.now(UTC)
        req.reviewed_by = actor.telegram_id
        await enqueue_telegram_event(
            session,
            event_type=TelegramEventType.folder_rename_result,
            recipient_telegram_id=user.telegram_id,
            dedup_key=f"folder-rename:{req.id}:approved:user:{user.telegram_id}",
            payload={"text": f"Заявка на переименование папки одобрена: {target}"},
            user_id=user.id,
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        raise ApiError(400, "invalid_request", "Проверьте корректность введённых данных.") from exc
    except Exception as exc:
        await session.rollback()
        raise ApiError(
            503, "yandex_disk_unavailable", "Не удалось переименовать папку Яндекс.Диска"
        ) from exc
    finally:
        await client.close()
    return rename_request_json(req, user)


@router.post("/folder-rename-requests/{request_id}/reject")
async def reject_folder_rename_request(
    request_id: int,
    body: FolderRenameRejectBody,
    actor: TelegramWebAppUser = Depends(admin_user_dep),
    session: AsyncSession = Depends(get_db),
) -> dict:
    req = await session.get(FolderRenameRequest, request_id)
    user = await session.get(User, req.user_id) if req else None
    if not req or not user:
        raise ApiError(404, "request_not_found", "Заявка не найдена.")
    if req.status != FolderRenameRequestStatus.pending:
        raise ApiError(409, "invalid_request_state", "Заявка уже обработана.")
    req.status = FolderRenameRequestStatus.rejected
    req.reject_reason = body.reason
    req.reviewed_at = datetime.now(UTC)
    req.reviewed_by = actor.telegram_id
    await enqueue_telegram_event(
        session,
        event_type=TelegramEventType.folder_rename_result,
        recipient_telegram_id=user.telegram_id,
        dedup_key=f"folder-rename:{req.id}:rejected:user:{user.telegram_id}",
        payload={"text": f"Заявка на переименование папки отклонена: {body.reason}"},
        user_id=user.id,
    )
    await session.commit()
    return rename_request_json(req, user)
