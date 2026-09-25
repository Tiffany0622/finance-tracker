import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import ApiError, audit
from app.core.config import settings
from app.core.db import transaction
from app.core.jobs import enqueue
from app.core.models import (
    Account,
    Attachment,
    CaptureDraft,
    Job,
    Receipt,
    ReceiptAttachment,
    ReceiptParseAttempt,
    TransactionAttachment,
    now,
)
from app.ledger.schemas import TransactionInput
from app.ledger.service import book_lock, create_transaction, fail, owned
from app.receipts import service as files

from .prompts import PROMPT_VERSION, explicit_currency
from .schemas import DraftAction, DraftEdit, DraftOutput, ParsedReceipt, Proposal


def draft_output(db: Session, row: CaptureDraft) -> DraftOutput:
    job = db.get(Job, row.job_id) if row.job_id else None
    status = row.status
    if status == "processing" and job and job.status in {"failed", "cancelled"}:
        status = "failed"
    return DraftOutput(
        id=row.id,
        revision=row.revision,
        status=status,
        source=row.source,
        proposal=Proposal.model_validate(row.proposal),
        warnings=row.warnings,
        parsed=ParsedReceipt.model_validate(row.parsed) if row.parsed else None,
        attachment_id=db.scalar(
            select(ReceiptAttachment.attachment_id)
            .where(ReceiptAttachment.receipt_id == row.receipt_id)
            .order_by(ReceiptAttachment.page_no)
            .limit(1)
        ),
        confirmed_transaction_id=row.confirmed_transaction_id,
        created_at=row.created_at,
        job_status=job.status if job else None,
        error_code=job.error_code if job else None,
    )


def editable(row: CaptureDraft, revision: int) -> None:
    if row.status in {"confirmed", "cancelled"}:
        fail("這份草稿已完成或取消，請重新載入。", "draft_closed", 409)
    if row.revision != revision:
        fail("草稿已更新，請重新載入再確認。", "revision_conflict", 409)


def queue_parse(db: Session, row: CaptureDraft) -> None:
    cfg = settings()
    if cfg.capture_provider == "disabled" or not cfg.capture_model:
        row.status = "needs_review"
        row.warnings = ["尚未啟用辨識，可先手動填寫。"]
        return
    row.status = "processing"
    receipt = owned(db, Receipt, row.owner_id, row.receipt_id)
    receipt.parse_status = "processing"
    row.job_id = enqueue(
        db,
        row.owner_id,
        "capture_parse",
        f"{row.id}:{row.revision}",
        {
            "draft_id": str(row.id),
            "revision": row.revision,
            "provider": cfg.capture_provider,
            "model": cfg.capture_model,
            "prompt_version": PROMPT_VERSION,
        },
    )


def store_image(db: Session, row: CaptureDraft, data: bytes, name: str, mime: str) -> None:
    # The same immutable publication and reference rules as manual receipt attachments.
    preview, detected, width, height = files.decode_image(data, mime)
    files.file_lock(db)
    if db.scalar(select(ReceiptAttachment).where(ReceiptAttachment.receipt_id == row.receipt_id)):
        return
    digest = hashlib.sha256(data).hexdigest()
    attachment = Attachment(
        owner_id=row.owner_id,
        upload_key=f"capture:{row.id}",
        request_hash=digest,
        sha256=digest,
        preview_sha256=hashlib.sha256(preview).hexdigest(),
        mime=detected,
        size_bytes=len(data),
        width=width,
        height=height,
        original_name=files.clean_name(name),
    )
    db.add(attachment)
    db.flush()
    try:
        files.publish(attachment.storage_key, data, preview)
    except (OSError, ValueError):
        fail("圖片無法保存，請檢查磁碟後重試。", "attachment_storage_unavailable", 503)
    db.add(
        ReceiptAttachment(
            owner_id=row.owner_id, receipt_id=row.receipt_id, attachment_id=attachment.id, page_no=1
        )
    )
    db.flush()


def new_draft(
    db: Session,
    owner: uuid.UUID,
    key: str,
    *,
    text: str = "",
    data: bytes | None = None,
    name: str = "receipt",
    mime: str = "",
    source: str = "web",
    chat: int | None = None,
    download: dict[str, Any] | None = None,
) -> CaptureDraft:
    book_lock(db, owner)
    hashed = hashlib.sha256(
        json.dumps(
            [text, hashlib.sha256(data).hexdigest() if data else None, name, download],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    previous = db.scalar(
        select(CaptureDraft).where(CaptureDraft.owner_id == owner, CaptureDraft.source_key == key)
    )
    if previous:
        if previous.request_hash != hashed:
            fail("相同上傳識別已用於不同內容。", "idempotency_conflict", 409)
        return previous
    receipt = Receipt(owner_id=owner)
    db.add(receipt)
    db.flush()
    row = CaptureDraft(
        owner_id=owner,
        receipt_id=receipt.id,
        source=source,
        source_key=key,
        request_hash=hashed,
        source_text=text,
        chat_id=chat,
        proposal=Proposal(note=text).model_dump(mode="json"),
    )
    db.add(row)
    db.flush()
    if data is not None:
        store_image(db, row, data, name, mime)
    if download:
        row.status = "processing"
        row.job_id = enqueue(
            db,
            owner,
            "telegram_download",
            f"download:{row.id}",
            {"draft_id": str(row.id), **download},
        )
    else:
        queue_parse(db, row)
    db.flush()
    return row


def save_draft(db: Session, owner: uuid.UUID, identity: uuid.UUID, body: DraftEdit) -> CaptureDraft:
    book_lock(db, owner)
    row = owned(db, CaptureDraft, owner, identity)
    editable(row, body.expected_revision)
    row.proposal = body.proposal.model_dump(mode="json")
    row.revision += 1
    row.status = "needs_review"
    return row


def confirm(
    db: Session, owner: uuid.UUID, identity: uuid.UUID, body: DraftAction, request: Any
) -> CaptureDraft:
    book_lock(db, owner)
    row = owned(db, CaptureDraft, owner, identity)
    if row.status == "confirmed" and body.expected_revision == row.revision - 1:
        return row
    editable(row, body.expected_revision)
    job = db.get(Job, row.job_id) if row.job_id else None
    if job and job.kind == "telegram_download" and not draft_output(db, row).attachment_id:
        fail("照片尚未保存，請先完成下載或重試。", "draft_download_pending", 409)
    if row.status == "processing":
        fail("辨識或下載尚未完成；可先儲存手動修正，再確認入帳。", "draft_processing", 409)
    if row.warnings and not body.acknowledge_warnings:
        fail("請先核對辨識提醒，勾選確認後再入帳。", "draft_warnings")
    proposal = Proposal.model_validate(row.proposal)
    if not proposal.account_id or not proposal.currency:
        fail("請選擇帳戶與收據幣別。", "draft_incomplete")
    account = owned(db, Account, owner, proposal.account_id)
    if account.currency != proposal.currency:
        fail("收據幣別必須與記帳帳戶相同，請確認換算後的實際扣款金額。", "currency_mismatch")
    try:
        input_data = TransactionInput.model_validate(proposal.model_dump(exclude={"currency"}))
    except ValidationError:
        fail("請填妥有效日期、正確金額、帳戶與分類。", "draft_incomplete")
    identity_tx = create_transaction(db, owner, input_data, f"capture:{row.id}")
    receipt = owned(db, Receipt, owner, row.receipt_id)
    receipt.transaction_id = identity_tx
    files.file_lock(db)
    for link in db.scalars(
        select(ReceiptAttachment).where(ReceiptAttachment.receipt_id == receipt.id)
    ):
        db.add(
            TransactionAttachment(
                owner_id=owner, transaction_id=identity_tx, attachment_id=link.attachment_id
            )
        )
    row.status = "confirmed"
    row.confirmed_transaction_id = identity_tx
    row.revision += 1
    audit(db, owner, "capture.confirm", str(row.id), request)
    from app.capture.items import carry_review_to_posted

    carry_review_to_posted(db, row, row.revision - 1)
    return row


def cancel(
    db: Session, owner: uuid.UUID, identity: uuid.UUID, revision: int, request: Any
) -> CaptureDraft:
    book_lock(db, owner)
    row = owned(db, CaptureDraft, owner, identity)
    if row.status == "cancelled" and row.revision == revision + 1:
        return row
    editable(row, revision)
    row.status = "cancelled"
    row.revision += 1
    audit(db, owner, "capture.cancel", str(row.id), request)
    return row


def retry_draft(
    db: Session, owner: uuid.UUID, identity: uuid.UUID, revision: int, request: Any
) -> CaptureDraft:
    """Web and Telegram share the same revision fence and durable retry path."""
    book_lock(db, owner)
    row = owned(db, CaptureDraft, owner, identity)
    editable(row, revision)
    job = db.get(Job, row.job_id) if row.job_id else None
    if row.status == "processing" and job and job.status not in {"failed", "cancelled"}:
        fail("工作仍在處理或等待重試。", "draft_processing", 409)
    row.revision += 1
    if job and job.kind == "telegram_download" and not draft_output(db, row).attachment_id:
        row.status = "processing"
        row.job_id = enqueue(
            db,
            row.owner_id,
            "telegram_download",
            f"download:{row.id}:{row.revision}",
            job.payload,
        )
    else:
        queue_parse(db, row)
    audit(db, owner, "capture.retry", str(row.id), request)
    return row


def validate_parsed(result: ParsedReceipt) -> list[str]:
    warnings = []
    for key, label in [
        ("merchant", "商家"),
        ("occurred_on", "日期"),
        ("currency", "幣別"),
        ("amount", "總額"),
    ]:
        if getattr(result, key) is None:
            warnings.append(f"無法確定{label}，請手動核對。")
    if result.amount is not None and Decimal(result.amount) <= 0:
        warnings.append("辨識總額不是正數，請修正。")
    if result.currency not in {"USD", "TWD", None}:
        warnings.append("收據幣別目前不支援，請確認實際帳戶扣款幣別與金額。")
    if result.subtotal is None:
        warnings.append("未取得小計，無法驗算品項與總額。")
    if result.subtotal is not None and result.amount is not None:
        if result.tax is None or result.discount is None or result.tip is None:
            warnings.append("稅金、小費或折扣不明，無法驗算總額。")
        elif Decimal(result.subtotal) + Decimal(result.tax) + Decimal(result.tip) - Decimal(
            result.discount
        ) != Decimal(result.amount):
            warnings.append("小計加稅及小費減折扣與總額不符，請核對。")
    if result.items:
        if any(i.line_total is None for i in result.items):
            warnings.append("部分品項金額不明。")
        elif result.subtotal is not None and sum(
            Decimal(i.line_total or "0") for i in result.items
        ) != Decimal(result.subtotal):
            warnings.append("品項加總與小計不符，請核對。")
        if any(
            i.quantity is not None
            and i.unit_price is not None
            and i.line_total is not None
            and Decimal(i.quantity) * Decimal(i.unit_price) != Decimal(i.line_total)
            for i in result.items
        ):
            warnings.append("品項數量乘單價與列金額不符，可能含折扣。")
    if result.uncertainty:
        warnings.append(result.uncertainty)
    return warnings


def lease(db: Session, owner: uuid.UUID, job_id: uuid.UUID, token: uuid.UUID) -> Job:
    job = db.scalar(select(Job).where(Job.id == job_id, Job.owner_id == owner).with_for_update())
    if (
        not job
        or job.kind not in {"capture_parse", "telegram_download", "telegram_send"}
        or job.status != "running"
        or job.lease_token != token
        or not job.lease_until
        or job.lease_until <= now()
    ):
        raise ApiError(409, "lease_lost", "工作已由其他程序接手，請重新取得。")
    return job


def apply_parse(db: Session, job: Job, result: ParsedReceipt | None, error: str | None) -> None:
    row = owned(db, CaptureDraft, job.owner_id, uuid.UUID(job.payload["draft_id"]))
    previous = db.scalar(
        select(ReceiptParseAttempt).where(
            ReceiptParseAttempt.job_id == job.id, ReceiptParseAttempt.attempt_no == job.attempts
        )
    )
    if not previous:
        db.add(
            ReceiptParseAttempt(
                owner_id=job.owner_id,
                draft_id=row.id,
                job_id=job.id,
                attempt_no=job.attempts,
                provider=job.payload["provider"],
                model=job.payload["model"],
                prompt_version=job.payload.get("prompt_version", 1),
                result=result.model_dump(mode="json") if result else None,
                error_code=error,
            )
        )
    if row.status != "processing" or row.revision != job.payload["revision"]:
        return  # An old result cannot overwrite a manual edit, cancellation or newer retry.
    receipt = owned(db, Receipt, row.owner_id, row.receipt_id)
    if error:
        receipt.parse_status = "failed"
        if job.attempts >= 5:
            row.status = "failed"
            row.warnings = ["辨識未完成，可手動填寫或稍後重試。"]
        return
    assert result
    proposal = Proposal.model_validate(row.proposal)
    proposal.amount = result.amount
    proposal.occurred_on = result.occurred_on
    proposal.merchant = result.merchant or ""
    currency = result.currency
    prompt_version = job.payload.get("prompt_version", 1)
    if prompt_version >= 2:
        currency = currency if currency == explicit_currency(row.source_text) else None
    proposal.currency = currency if currency in {"USD", "TWD"} else None  # type: ignore[assignment]
    row.proposal = proposal.model_dump(mode="json")
    row.parsed = result.model_dump(mode="json")
    row.warnings = validate_parsed(result)
    if prompt_version >= 2 and result.currency and proposal.currency is None:
        row.warnings = [
            *row.warnings,
            "AI 幣別僅供參考，請對照收據手動選擇；不以地址或 $ 符號推定。",
        ]
    row.status = "needs_review"
    row.revision += 1
    receipt.parse_status = "parsed"


def create_web(owner: uuid.UUID, key: str, request: Any, **kwargs: Any) -> DraftOutput:
    with transaction() as db:
        row = new_draft(db, owner, "web:" + key, **kwargs)
        audit(db, owner, "capture.create", str(row.id), request)
        return draft_output(db, row)
