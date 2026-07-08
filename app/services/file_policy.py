from app.config import Settings
from app.db.models import User


def can_user_upload(user: User) -> bool:
    return user.status.value == "active"


def validate_size(size_bytes: int, settings: Settings) -> bool:
    return size_bytes <= settings.max_file_size_bytes


def folder_allowed(user: User, folder: str) -> bool:
    return bool(
        user.root_folder and folder.startswith(user.root_folder) and folder in user.allowed_folders
    )
