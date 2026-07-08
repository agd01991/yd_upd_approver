from fastapi import APIRouter, Depends

from app.api.deps import current_user_dep, settings_dep
from app.config import Settings
from app.db.models import User
from app.services.yandex_disk import YandexDiskClient, YandexDiskError

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
    settings: Settings = Depends(settings_dep),
) -> dict:
    user, _ = current
    if not user.root_folder:
        return {"message": "Папка ещё не назначена", "items": []}
    client = YandexDiskClient(settings.yandex_disk_token)
    try:
        try:
            items = await client.list_files(user.root_folder)
        except FileNotFoundError:
            return {"message": "Папка ещё не создана", "items": []}
        except YandexDiskError as exc:
            return {"message": f"Не удалось получить список файлов: {exc}", "items": []}
    finally:
        await client.close()
    return {"items": [safe_item(i) for i in items]}
