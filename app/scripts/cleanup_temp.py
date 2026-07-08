import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db.models import UploadRequest, UploadStatus
from app.db.session import SessionLocal

CLEANUP_STATUSES = {UploadStatus.uploaded, UploadStatus.rejected, UploadStatus.deleted_temp}
KEEP_STATUSES = {
    UploadStatus.pending_approval,
    UploadStatus.approved,
    UploadStatus.uploading,
    UploadStatus.failed,
}


async def cleanup_temp() -> int:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=settings.rejected_retention_days)
    deleted = 0
    async with SessionLocal() as session:
        requests = (
            await session.scalars(
                select(UploadRequest).where(
                    UploadRequest.status.in_(CLEANUP_STATUSES),
                    UploadRequest.created_at < cutoff,
                )
            )
        ).all()
        for request in requests:
            if request.status in KEEP_STATUSES or not request.local_path:
                continue
            path = Path(request.local_path)
            if path.exists():
                path.unlink()
                deleted += 1
            request.status = UploadStatus.deleted_temp
        await session.commit()
    return deleted


def main() -> None:
    deleted = asyncio.run(cleanup_temp())
    print(f"Deleted temporary files: {deleted}")


if __name__ == "__main__":
    main()
