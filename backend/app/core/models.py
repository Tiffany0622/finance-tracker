import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
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


class Owned(Identity):
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))


class Attachment(Owned, Base):
    __tablename__ = "attachments"
    storage_key: Mapped[uuid.UUID] = mapped_column(unique=True, default=uuid.uuid4)
    upload_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    sha256: Mapped[str] = mapped_column(String(64))
    preview_sha256: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    width: Mapped[int]
    height: Mapped[int]
    original_name: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(default="receipt")
    status: Mapped[str] = mapped_column(default="ready")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("owner_id", "upload_key"),
        CheckConstraint("status IN ('ready','deleting')"),
        CheckConstraint("(status = 'deleting') = (deleted_at IS NOT NULL)"),
        CheckConstraint("purpose = 'receipt'"),
        CheckConstraint("mime IN ('image/jpeg','image/png','image/heic')"),
        CheckConstraint("size_bytes > 0 AND size_bytes <= 20971520"),
        CheckConstraint("width > 0 AND height > 0 AND width::bigint * height <= 50000000"),
    )


class Receipt(Owned, Base):
    __tablename__ = "receipts"
    transaction_id: Mapped[uuid.UUID]
    parse_status: Mapped[str] = mapped_column(default="not_requested")
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("owner_id", "transaction_id"),
        ForeignKeyConstraint(
            ["owner_id", "transaction_id"], ["transactions.owner_id", "transactions.id"]
        ),
        CheckConstraint("parse_status = 'not_requested'"),
    )


class TransactionAttachment(Base):
    __tablename__ = "transaction_attachments"
    owner_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    attachment_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(default="receipt")
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "transaction_id"], ["transactions.owner_id", "transactions.id"]
        ),
        ForeignKeyConstraint(
            ["owner_id", "attachment_id"], ["attachments.owner_id", "attachments.id"]
        ),
        CheckConstraint("role = 'receipt'"),
    )


class ReceiptAttachment(Base):
    __tablename__ = "receipt_attachments"
    owner_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    receipt_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    attachment_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    page_no: Mapped[int]
    __table_args__ = (
        UniqueConstraint("receipt_id", "page_no"),
        ForeignKeyConstraint(["owner_id", "receipt_id"], ["receipts.owner_id", "receipts.id"]),
        ForeignKeyConstraint(
            ["owner_id", "attachment_id"], ["attachments.owner_id", "attachments.id"]
        ),
        CheckConstraint("page_no > 0"),
    )


class Account(Owned, Base):
    __tablename__ = "accounts"
    name: Mapped[str]
    kind: Mapped[str]
    currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"))
    include_in_net_worth: Mapped[bool] = mapped_column(default=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(default=1)
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        CheckConstraint("kind IN ('bank','cash','credit_card')"),
        CheckConstraint("revision > 0"),
    )


class Category(Owned, Base):
    __tablename__ = "categories"
    name: Mapped[str]
    kind: Mapped[str]
    parent_id: Mapped[uuid.UUID | None]
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(default=1)
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        ForeignKeyConstraint(["owner_id", "parent_id"], ["categories.owner_id", "categories.id"]),
        CheckConstraint("kind IN ('income','expense')"),
    )


class LedgerAccount(Owned, Base):
    __tablename__ = "ledger_accounts"
    account_id: Mapped[uuid.UUID | None]
    code: Mapped[str]
    ledger_class: Mapped[str]
    currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"))
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("owner_id", "code"),
        UniqueConstraint("account_id"),
        ForeignKeyConstraint(["owner_id", "account_id"], ["accounts.owner_id", "accounts.id"]),
        CheckConstraint("ledger_class IN ('asset','liability','income','expense','equity')"),
    )


class Transaction(Owned, Base):
    __tablename__ = "transactions"
    kind: Mapped[str]
    status: Mapped[str] = mapped_column(default="posted")
    current_entry_id: Mapped[uuid.UUID | None]
    refund_of_id: Mapped[uuid.UUID | None]
    revision: Mapped[int] = mapped_column(default=1)
    note: Mapped[str] = mapped_column(default="")
    merchant: Mapped[str] = mapped_column(default="")
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        ForeignKeyConstraint(
            ["owner_id", "refund_of_id"], ["transactions.owner_id", "transactions.id"]
        ),
        ForeignKeyConstraint(
            ["owner_id", "id", "current_entry_id"],
            ["journal_entries.owner_id", "journal_entries.transaction_id", "journal_entries.id"],
            name="fk_transaction_current_entry",
            use_alter=True,
        ),
        CheckConstraint("kind IN ('income','expense','transfer','refund','opening','adjustment')"),
        CheckConstraint("status IN ('posted','voided')"),
        CheckConstraint("revision > 0"),
    )


class JournalEntry(Owned, Base):
    __tablename__ = "journal_entries"
    transaction_id: Mapped[uuid.UUID]
    revision_no: Mapped[int]
    entry_role: Mapped[str]
    occurred_on: Mapped[date] = mapped_column(Date)
    book_currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"))
    reverses_entry_id: Mapped[uuid.UUID | None]
    reason: Mapped[str] = mapped_column(default="")
    creation_txid: Mapped[int] = mapped_column(BigInteger, server_default=text("txid_current()"))
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("owner_id", "transaction_id", "id"),
        UniqueConstraint("transaction_id", "revision_no", "entry_role"),
        UniqueConstraint("reverses_entry_id"),
        ForeignKeyConstraint(
            ["owner_id", "transaction_id"], ["transactions.owner_id", "transactions.id"]
        ),
        ForeignKeyConstraint(
            ["owner_id", "reverses_entry_id"], ["journal_entries.owner_id", "journal_entries.id"]
        ),
        CheckConstraint("entry_role IN ('original','reversal','replacement')"),
        Index("ix_entries_owner_date", "owner_id", "occurred_on"),
    )


class Posting(Owned, Base):
    __tablename__ = "postings"
    entry_id: Mapped[uuid.UUID]
    line_no: Mapped[int]
    ledger_account_id: Mapped[uuid.UUID]
    currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"))
    amount_signed: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    book_amount_signed: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    component: Mapped[str]
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("owner_id", "entry_id", "id"),
        UniqueConstraint("entry_id", "line_no"),
        ForeignKeyConstraint(
            ["owner_id", "entry_id"], ["journal_entries.owner_id", "journal_entries.id"]
        ),
        ForeignKeyConstraint(
            ["owner_id", "ledger_account_id"], ["ledger_accounts.owner_id", "ledger_accounts.id"]
        ),
        CheckConstraint(
            "amount_signed <> 0 AND abs(amount_signed) < 1e18 AND abs(book_amount_signed) < 1e18 AND fx_rate > 0 AND fx_rate < 1e18"
        ),
        Index("ix_postings_ledger", "ledger_account_id"),
    )


class TransactionSplit(Owned, Base):
    __tablename__ = "transaction_splits"
    entry_id: Mapped[uuid.UUID]
    posting_id: Mapped[uuid.UUID]
    category_id: Mapped[uuid.UUID]
    amount_signed: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    book_amount_signed: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    __table_args__ = (
        UniqueConstraint("owner_id", "id"),
        ForeignKeyConstraint(
            ["owner_id", "entry_id", "posting_id"],
            ["postings.owner_id", "postings.entry_id", "postings.id"],
        ),
        ForeignKeyConstraint(["owner_id", "category_id"], ["categories.owner_id", "categories.id"]),
        CheckConstraint(
            "amount_signed <> 0 AND abs(amount_signed) < 1e18 AND abs(book_amount_signed) < 1e18"
        ),
    )


class FxQuote(Owned, Base):
    __tablename__ = "fx_quotes"
    currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"))
    book_currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"))
    effective_on: Mapped[date] = mapped_column(Date)
    rate: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    source: Mapped[str]
    __table_args__ = (
        CheckConstraint("rate > 0 AND rate < 1e18"),
        Index("ix_quotes_owner_currency_date", "owner_id", "currency", "effective_on"),
    )


class ReportSnapshot(Owned, Base):
    __tablename__ = "report_snapshots"
    document: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_hash: Mapped[str]
    __table_args__ = (UniqueConstraint("owner_id", "id"),)
