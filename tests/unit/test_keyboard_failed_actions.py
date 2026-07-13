from app.bot.keyboards import upload_keyboard
from app.db.models import UploadStatus


def _actions(markup):
    return [row[0].callback_data for row in markup.inline_keyboard]


def test_failed_keyboard_contains_edit_and_retry_actions() -> None:
    callbacks = _actions(upload_keyboard(1, UploadStatus.failed))
    text = "\n".join(callbacks)
    for action in ["rename_stem", "rename_extension", "folder", "retry", "copy", "overwrite"]:
        assert action in text
    assert "approve" not in text


def test_non_failed_final_states_do_not_get_edit_actions() -> None:
    for status in [
        UploadStatus.approved,
        UploadStatus.uploading,
        UploadStatus.uploaded,
        UploadStatus.rejected,
    ]:
        text = "\n".join(_actions(upload_keyboard(1, status)))
        assert "rename_stem" not in text
        assert "rename_extension" not in text
        assert "folder" not in text
