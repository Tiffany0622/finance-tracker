import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Identity:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class User(Identity, Base):
    __tablename__ = "users"
    login_name: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str]
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Currency(Base):
    __tablename__ = "currencies"
    code: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str]
    amount_scale: Mapped[int]
    __table_args__ = (CheckConstraint("amount_scale BETWEEN 0 AND 18"),)


class BookSettings(Base):
    __tablename__ = "book_settings"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    book_currency: Mapped[str | None] = mapped_column(ForeignKey("currencies.code"))
    timezone: Mapped[str | None]
    locale: Mapped[str] = mapped_column(default="zh-TW")
    setup_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ledger_revision: Mapped[int] = mapped_column(BigInteger, default=0)
    valuation_revision: Mapped[int] = mapped_column(BigInteger, default=0)
    settings_revision: Mapped[int] = mapped_column(BigInteger, default=0)


class SessionFamily(Identity, Base):
    __tablename__ = "session_families"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuthSession(Identity, Base):
    __tablename__ = "auth_sessions"
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session_families.id"), index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TotpCredential(Base):
    __tablename__ = "totp_credentials"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    encrypted_secret: Mapped[str]
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_step: Mapped[int] = mapped_column(BigInteger, default=-1)
    recovery_hashes: Mapped[list[str]] = mapped_column(JSONB, default=list)


class ApiToken(Identity, Base):
    __tablename__ = "api_tokens"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginBucket(Base):
    __tablename__ = "login_buckets"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(Identity, Base):
    __tablename__ = "audit_logs"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str]
    entity_type: Mapped[str]
    entity_id: Mapped[str]
    request_id: Mapped[str]
    redacted_diff: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class IdempotencyRecord(Identity, Base):
    __tablename__ = "idempotency_records"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    operation: Mapped[str]
    key: Mapped[str]
    request_hash: Mapped[str]
    resource_id: Mapped[uuid.UUID]
    response_code: Mapped[int]
    __table_args__ = (UniqueConstraint("owner_id", "operation", "key"),)


class Job(Identity, Base):
    __tablename__ = "jobs"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str]
    logical_key: Mapped[str]
    payload_version: Mapped[int] = mapped_column(default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(default="queued")
    attempts: Mapped[int] = mapped_column(default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None]
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None]
    __table_args__ = (
        UniqueConstraint("owner_id", "kind", "logical_key"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','retry_wait','failed','cancelled')"
        ),
        CheckConstraint("attempts >= 0"),
        Index("ix_jobs_due", "status", "run_after"),
    )


class OutboxEvent(Identity, Base):
    __tablename__ = "outbox_events"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str]
    entity_id: Mapped[uuid.UUID]
    entity_revision: Mapped[int]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobEffect(Base):
    """Probe effect proves retries cannot apply the same DB side effect twice."""

    __tablename__ = "job_effects"
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BackupRun(Identity, Base):
    __tablename__ = "backup_runs"
    status: Mapped[str]
    target: Mapped[str]
    app_commit: Mapped[str]
    schema_version: Mapped[str]
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manifest_hash: Mapped[str | None]
    error_code: Mapped[str | None]
    last_restore_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
