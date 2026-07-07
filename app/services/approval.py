from app.db.models import UploadStatus

ALLOWED_TRANSITIONS = {
    UploadStatus.new: {UploadStatus.stored},
    UploadStatus.stored: {UploadStatus.pending_approval},
    UploadStatus.pending_approval: {
        UploadStatus.approved,
        UploadStatus.rejected,
        UploadStatus.cancelled,
    },
    UploadStatus.approved: {UploadStatus.uploading},
    UploadStatus.uploading: {UploadStatus.uploaded, UploadStatus.failed},
    UploadStatus.failed: {UploadStatus.uploading, UploadStatus.rejected},
    UploadStatus.uploaded: {UploadStatus.deleted_temp},
}


def can_transition(old: UploadStatus, new: UploadStatus) -> bool:
    return new in ALLOWED_TRANSITIONS.get(old, set())


def require_transition(old: UploadStatus, new: UploadStatus) -> None:
    if not can_transition(old, new):
        msg = f"Illegal upload status transition: {old.value} -> {new.value}"
        raise ValueError(msg)
