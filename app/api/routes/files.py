from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user_dep, get_db, settings_dep
from app.api.errors import ApiError
from app.config import Settings
from app.db.models import User, UserStatus
from app.services.user_folders import ensure_user_folder_for_current_root
from app.services.yandex_disk import (
    ConflictError,
    InsufficientStorageError,
    YandexAuthError,
    YandexDiskClient,
    YandexDiskError,
    YandexNetworkError,
)

router = APIRouter(prefix="/files")


def safe_item(item: dict) -> dict:
    return {
        "name": item.get("name"),
        "type": item.get("type"),
        "size": item.get("size"),
        "modified": item.get("modified"),
    }


@router.get("")
async def list_files(
    current: tuple[User, bool] = Depends(current_user_dep),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    user, _ = current
    if user.status != UserStatus.active:
        return {"message": "Папка ещё не назначена", "items": []}
    client = YandexDiskClient(settings.yandex_disk_token)
    try:
        folder = await ensure_user_folder_for_current_root(session, user, settings, client)
        await session.commit()
        try:
            items = await client.list_files(folder)
        except FileNotFoundError:
            return {"message": "Папка ещё не создана", "items": []}
        except YandexAuthError as exc:
            raise ApiError(
                503, "yandex_disk_unavailable", "Яндекс.Диск временно недоступен."
            ) from exc
        except YandexNetworkError as exc:
            raise ApiError(
                503, "yandex_disk_unavailable", "Яндекс.Диск временно недоступен."
            ) from exc
        except InsufficientStorageError as exc:
            raise ApiError(
                507,
                "yandex_disk_insufficient_storage",
                "На Яндекс.Диске недостаточно свободного места.",
            ) from exc
        except ConflictError as exc:
            raise ApiError(409, "resource_conflict", "Конфликт ресурса на Яндекс.Диске.") from exc
        except YandexDiskError as exc:
            raise ApiError(
                503, "yandex_disk_unavailable", "Не удалось получить список файлов."
            ) from exc
    except ApiError:
        raise
    except Exception as exc:
        if hasattr(session, "rollback"):
            await session.rollback()
        raise ApiError(
            503, "yandex_disk_unavailable", "Не удалось подготовить папку на Яндекс.Диске"
        ) from exc
    finally:
        await client.close()
    return {"items": [safe_item(i) for i in items]}
