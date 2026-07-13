from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserStatus(StrEnum):
    pending = "pending"
    active = "active"
    rejected = "rejected"
    blocked = "blocked"


class UploadSource(StrEnum):
    telegram = "telegram"
    mini_app = "mini_app"


class UploadMode(StrEnum):
    normal = "normal"
    copy = "copy"
    overwrite = "overwrite"


class UploadStatus(StrEnum):
    new = "new"
    stored = "stored"
    pending_approval = "pending_approval"
    approved = "approved"
    uploading = "uploading"
    uploaded = "uploaded"
    rejected = "rejected"
    failed = "failed"
    cancelled = "cancelled"
    deleted_temp = "deleted_temp"


class FolderRenameRequestStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"


class TelegramOutboxStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    sent = "sent"
    discarded = "discarded"
    dead = "dead"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus), default=UserStatus.pending, index=True
    )
    root_folder: Mapped[str | None] = mapped_column(String(1024))
    folder_name: Mapped[str | None] = mapped_column(String(512))
    contract_number: Mapped[str | None] = mapped_column(String(128))
    contract_date: Mapped[str | None] = mapped_column(String(64))
    contract_full_name: Mapped[str | None] = mapped_column(String(512))
    allowed_folders: Mapped[list[str]] = mapped_column(JSONB, default=list)
    quota_mb: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int | None] = mapped_column(BigInteger)

    requests: Mapped[list["UploadRequest"]] = relationship(back_populates="user")
    folder_rename_requests: Mapped[list["FolderRenameRequest"]] = relationship(
        back_populates="user"
    )


class FolderRenameRequest(Base):
    __tablename__ = "folder_rename_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    requested_folder_name: Mapped[str] = mapped_column(String(512))
    contract_number: Mapped[str | None] = mapped_column(String(128))
    contract_date: Mapped[str | None] = mapped_column(String(64))
    contract_full_name: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[FolderRenameRequestStatus] = mapped_column(
        Enum(FolderRenameRequestStatus), default=FolderRenameRequestStatus.pending, index=True
    )
    source_folder: Mapped[str | None] = mapped_column(String(1024))
    target_folder: Mapped[str | None] = mapped_column(String(1024))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger)

    user: Mapped[User] = relationship(back_populates="folder_rename_requests")


class UploadRequest(Base):
    __tablename__ = "upload_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source: Mapped[UploadSource] = mapped_column(
        Enum(UploadSource), default=UploadSource.telegram, index=True
    )
    telegram_file_id: Mapped[str | None] = mapped_column(String(512))
    telegram_file_unique_id: Mapped[str | None] = mapped_column(String(512))
    source_event_key: Mapped[str | None] = mapped_column(String(256), unique=True, index=True)
    original_filename: Mapped[str] = mapped_column(String(512))
    safe_filename: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    caption: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str] = mapped_column(String(2048))
    target_folder: Mapped[str] = mapped_column(String(1024))
    target_path: Mapped[str] = mapped_column(String(1536))
    status: Mapped[UploadStatus] = mapped_column(
        Enum(UploadStatus), default=UploadStatus.new, index=True
    )
    admin_comment: Mapped[str | None] = mapped_column(Text)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int | None] = mapped_column(BigInteger)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    upload_mode: Mapped[UploadMode] = mapped_column(
        Enum(UploadMode), default=UploadMode.normal, index=True
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    worker_token: Mapped[str | None] = mapped_column(String(64), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="requests")


class TelegramOutbox(Base):
    __tablename__ = "telegram_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    recipient_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    dedup_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    status: Mapped[TelegramOutboxStatus] = mapped_column(
        Enum(TelegramOutboxStatus), default=TelegramOutboxStatus.pending, index=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    lock_token: Mapped[str | None] = mapped_column(String(64), index=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("upload_requests.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(128), index=True)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("upload_requests.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
