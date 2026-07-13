from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.db.models import UploadStatus
from app.services.upload_queue import UploadQueueError, reject_upload_request


class FakeResult:
    def __init__(self, request):
        self.request = request

    def scalar_one_or_none(self):
        return self.request


class FakeSession:
    def __init__(self, request, user):
        self.request = request
        self.user = user
        self.committed = False
        self.added = []

    async def execute(self, stmt):  # noqa: ANN001
        self.stmt = stmt
        return FakeResult(self.request)

    async def get(self, model, ident):  # noqa: ANN001
        return self.user if ident == self.user.id else None

    def add(self, obj):  # noqa: ANN001
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def make_request(status: UploadStatus):
    return SimpleNamespace(
        id=1,
        user_id=2,
        status=status,
        rejected_at=None,
        reject_reason=None,
        worker_token="worker-token",
        lease_expires_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("status", [UploadStatus.pending_approval, UploadStatus.failed])
async def test_locked_reject_service_allows_only_moderatable_statuses(status: UploadStatus) -> None:
    request = make_request(status)
    session = FakeSession(request, SimpleNamespace(id=2))

    result = await reject_upload_request(session, request.id, 100, "x" * 1100)

    assert result is request
    assert request.status == UploadStatus.rejected
    assert request.reject_reason == "x" * 1000
    assert session.committed
    assert len(session.added) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status",
    [
        UploadStatus.approved,
        UploadStatus.uploading,
        UploadStatus.uploaded,
        UploadStatus.rejected,
        UploadStatus.cancelled,
        UploadStatus.deleted_temp,
    ],
)
async def test_locked_reject_service_blocks_queue_and_terminal_statuses(
    status: UploadStatus,
) -> None:
    request = make_request(status)
    session = FakeSession(request, SimpleNamespace(id=2))

    with pytest.raises(UploadQueueError):
        await reject_upload_request(session, request.id, 100, "nope")

    assert request.status == status
    assert request.worker_token == "worker-token"
    assert request.lease_expires_at is not None
    assert not session.committed
    assert session.added == []
