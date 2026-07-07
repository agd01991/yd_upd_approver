import pytest

from app.db.models import UploadStatus
from app.services.approval import can_transition, require_transition


def test_status_transitions() -> None:
    assert can_transition(UploadStatus.pending_approval, UploadStatus.approved)
    assert can_transition(UploadStatus.uploading, UploadStatus.failed)
    assert not can_transition(UploadStatus.uploaded, UploadStatus.uploading)
    with pytest.raises(ValueError):
        require_transition(UploadStatus.rejected, UploadStatus.uploading)
