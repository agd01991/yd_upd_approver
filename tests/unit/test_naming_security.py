from datetime import date

from app.config import Settings
from app.db.models import User, UserStatus
from app.services.file_policy import can_user_upload, folder_allowed
from app.services.naming import copy_filename, join_disk_path, sanitize_filename, user_folder
from app.utils.security import is_admin


def test_sanitize_filename_blocks_traversal_and_slashes() -> None:
    assert sanitize_filename("../bad/name.txt") == "name.txt"
    assert sanitize_filename("..") == "file"
    assert sanitize_filename("a\x00b/c?.txt") == "c_.txt"


def test_safe_disk_path_and_user_folder() -> None:
    folder = user_folder("disk:/Telegram Uploads", 123, "Ivan Petrov", None)
    assert folder == "disk:/Telegram Uploads/123_ivan_petrov/"
    assert join_disk_path(folder, "../x.txt") == "disk:/Telegram Uploads/123_ivan_petrov/x.txt"


def test_copy_filename_format() -> None:
    assert (
        copy_filename("report.pdf", "REQ-000001", date(2026, 7, 7))
        == "report__2026-07-07__REQ-000001.pdf"
    )


def test_user_and_admin_rights() -> None:
    settings = Settings(telegram_admin_ids=[1])
    assert is_admin(1, settings)
    assert not is_admin(2, settings)
    user = User(
        telegram_id=2,
        status=UserStatus.active,
        root_folder="disk:/r/2/",
        allowed_folders=["disk:/r/2/"],
    )
    assert can_user_upload(user)
    assert folder_allowed(user, "disk:/r/2/")
    assert not folder_allowed(user, "disk:/r/other/")
