"""Local receipt files: publish before DB commit; never expose storage paths."""

import hashlib
import io
import json
import os
import shutil
import threading
import unicodedata
import uuid
import warnings
from datetime import timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core.auth import ApiError, audit
from app.core.config import settings
from app.core.db import transaction
from app.core.models import (
    Attachment,
    Receipt,
    ReceiptAttachment,
    Transaction,
    TransactionAttachment,
    now,
)
from app.ledger.service import book_lock, fail, owned
from app.receipts.schemas import AttachmentOutput

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 50_000_000
MAX_ATTACHMENTS = 20
FILE_LOCK = 724805
MIMES = {"JPEG": "image/jpeg", "PNG": "image/png", "HEIF": "image/heic"}
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/heic": "heic"}
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
register_heif_opener(thumbnails=False)
_decode_lock = threading.Lock()


def clean_name(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if not unicodedata.category(c).startswith("C"))
    return name.strip()[:200] or "receipt"


def decode_image(data: bytes, claimed_mime: str) -> tuple[bytes, str, int, int]:
    if not data or len(data) > MAX_BYTES:
        fail("每張圖片須介於 1 byte 與 20 MiB 之間。", "attachment_size", 413)
    with _decode_lock, warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data), formats=list(MIMES)) as source:
                mime = MIMES.get(source.format or "")
                if not mime:
                    fail("僅支援 JPEG、PNG、HEIC 圖片；PDF 尚未支援。", "attachment_type", 415)
                accepted = {mime, "application/octet-stream", ""}
                if mime == "image/heic":
                    accepted.add("image/heif")
                if claimed_mime not in accepted:
                    fail("檔案內容與圖片類型不符。", "attachment_type", 415)
                if source.width * source.height > MAX_PIXELS:
                    fail("圖片超過 5,000 萬像素，請縮小後再上傳。", "attachment_pixels")
                if getattr(source, "n_frames", 1) != 1:
                    fail("請將動態或多影格圖片拆成個別照片後上傳。", "attachment_frames")
                source.load()  # Reject truncated/corrupt data before publication.
                oriented = ImageOps.exif_transpose(source)
                width, height = oriented.size
                oriented.thumbnail((1600, 1600))
                rgba = oriented.convert("RGBA")
                preview = Image.new("RGB", rgba.size, "white")
                preview.paste(rgba, mask=rgba.getchannel("A"))
                output = io.BytesIO()
                # A new image excludes EXIF, GPS, XMP and the original filename.
                preview.save(output, format="JPEG", quality=88)
                return output.getvalue(), mime, width, height
        except ApiError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            fail("圖片超過 5,000 萬像素，請縮小後再上傳。", "attachment_pixels")
        except (OSError, ValueError, SyntaxError):
            fail(
                "無法解碼圖片。請使用完整的 JPEG、PNG 或 HEIC；PDF 尚未支援。",
                "attachment_invalid",
                415,
            )


def file_lock(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": FILE_LOCK})


def root_path(data_dir: Path | None = None) -> Path:
    root = (data_dir or settings().data_dir) / "attachments"
    if root.is_symlink():
        raise ValueError("attachment_storage_invalid")
    return root


def file_path(key: uuid.UUID, name: str, data_dir: Path | None = None) -> Path:
    root = root_path(data_dir)
    path = root / "objects" / str(key) / name
    if any(p.is_symlink() for p in (root / "objects", path.parent, path)):
        raise ValueError("attachment_storage_invalid")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("attachment_storage_invalid")
    return path


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(key: uuid.UUID, original: bytes, preview: bytes) -> None:
    root = root_path()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging_root = root / "staging"
    objects = root / "objects"
    if staging_root.is_symlink() or objects.is_symlink():
        raise ValueError("attachment_storage_invalid")
    staging_root.mkdir(exist_ok=True, mode=0o700)
    objects.mkdir(exist_ok=True, mode=0o700)
    stage = staging_root / str(key)
    stage.mkdir(mode=0o700)
    for name, data in (("original", original), ("preview.jpg", preview)):
        fd = os.open(stage / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    sync_dir(stage)
    stage.rename(objects / str(key))
    sync_dir(objects)
    sync_dir(staging_root)
    sync_dir(root)


def available(row: Attachment) -> bool:
    try:
        return all(file_path(row.storage_key, n).is_file() for n in ("original", "preview.jpg"))
    except (ValueError, OSError):
        return False


def output(row: Attachment, link: ReceiptAttachment) -> AttachmentOutput:
    return AttachmentOutput(
        id=row.id,
        receipt_id=link.receipt_id,
        original_name=row.original_name,
        mime=row.mime,
        size_bytes=row.size_bytes,
        width=row.width,
        height=row.height,
        page_no=link.page_no,
        created_at=row.created_at,
        status="ready" if available(row) else "missing",
    )


def list_attachments(db: Session, owner: uuid.UUID, txn_id: uuid.UUID) -> list[AttachmentOutput]:
    owned(db, Transaction, owner, txn_id)
    rows = db.execute(
        select(Attachment, ReceiptAttachment)
        .join(ReceiptAttachment, ReceiptAttachment.attachment_id == Attachment.id)
        .join(Receipt, Receipt.id == ReceiptAttachment.receipt_id)
        .where(
            Attachment.owner_id == owner,
            Receipt.transaction_id == txn_id,
            Attachment.status == "ready",
        )
        .order_by(ReceiptAttachment.page_no)
    )
    return [output(row, link) for row, link in rows]


def store_upload(
    owner: uuid.UUID,
    txn_id: uuid.UUID,
    key: str,
    name: str,
    data: bytes,
    claimed_mime: str,
    request: Any,
) -> AttachmentOutput:
    name = clean_name(name)
    digest = hashlib.sha256(data).hexdigest()
    request_hash = hashlib.sha256(json.dumps([str(txn_id), name, digest]).encode()).hexdigest()
    # Decode before holding the DB/file lock. Authentication/ownership already checked by route.
    preview, mime, width, height = decode_image(data, claimed_mime)
    with transaction() as db:
        book_lock(db, owner)
        txn = owned(db, Transaction, owner, txn_id)
        file_lock(db)
        existing = db.scalar(
            select(Attachment).where(Attachment.owner_id == owner, Attachment.upload_key == key)
        )
        if existing:
            if existing.request_hash != request_hash:
                fail("這次上傳識別已用於其他圖片，請重新選取。", "idempotency_conflict", 409)
            if existing.status != "ready":
                fail("這張附件已移除，請重新選取圖片。", "attachment_removed", 409)
            link = db.scalar(
                select(ReceiptAttachment).where(ReceiptAttachment.attachment_id == existing.id)
            )
            if not link:
                fail("附件關聯已變更，請重新載入。", "attachment_conflict", 409)
            return output(existing, link)
        if txn.status != "posted":
            fail("已作廢的交易不能新增附件。", "transaction_voided", 409)
        receipt = db.scalar(
            select(Receipt).where(Receipt.owner_id == owner, Receipt.transaction_id == txn_id)
        )
        if not receipt:
            receipt = Receipt(owner_id=owner, transaction_id=txn_id)
            db.add(receipt)
            db.flush()
        count = (
            db.scalar(
                select(func.count())
                .select_from(ReceiptAttachment)
                .where(ReceiptAttachment.receipt_id == receipt.id)
            )
            or 0
        )
        if count >= MAX_ATTACHMENTS:
            fail("每筆交易最多 20 張收據圖片。", "attachment_limit", 409)
        page = (
            db.scalar(
                select(func.max(ReceiptAttachment.page_no)).where(
                    ReceiptAttachment.receipt_id == receipt.id
                )
            )
            or 0
        ) + 1
        row = Attachment(
            owner_id=owner,
            upload_key=key,
            request_hash=request_hash,
            sha256=digest,
            preview_sha256=hashlib.sha256(preview).hexdigest(),
            mime=mime,
            size_bytes=len(data),
            width=width,
            height=height,
            original_name=name,
        )
        db.add(row)
        db.flush()
        try:
            publish(row.storage_key, data, preview)
        except (OSError, ValueError):
            fail(
                "圖片無法保存，請確認磁碟空間與資料夾權限後重試。",
                "attachment_storage_unavailable",
                503,
            )
        link = ReceiptAttachment(
            owner_id=owner, receipt_id=receipt.id, attachment_id=row.id, page_no=page
        )
        db.add_all(
            [
                link,
                TransactionAttachment(owner_id=owner, transaction_id=txn_id, attachment_id=row.id),
            ]
        )
        db.flush()
        audit(db, owner, "attachment.create", str(row.id), request)
        return output(row, link)


def read_content(owner: uuid.UUID, identity: uuid.UUID, preview: bool) -> tuple[bytes, str, str]:
    # Read into bounded memory while locked so GC cannot remove a file mid-response.
    with transaction() as db:
        file_lock(db)
        row = owned(db, Attachment, owner, identity)
        if row.status != "ready":
            fail("找不到附件。", "not_found", 404)
        try:
            path = file_path(row.storage_key, "preview.jpg" if preview else "original")
            with path.open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
            expected = row.preview_sha256 if preview else row.sha256
            if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("attachment_integrity")
        except (OSError, ValueError):
            fail(
                "附件遺失或校驗失敗，請從完整備份還原，或移除後重新上傳。",
                "attachment_missing",
                409,
            )
        return data, "image/jpeg" if preview else row.mime, row.original_name


def remove_attachment(
    owner: uuid.UUID, txn_id: uuid.UUID, identity: uuid.UUID, request: Any
) -> None:
    with transaction() as db:
        owned(db, Transaction, owner, txn_id)
        book_lock(db, owner)
        file_lock(db)
        row = owned(db, Attachment, owner, identity)
        link = db.get(TransactionAttachment, (owner, txn_id, identity))
        if not link:
            # Idempotent removal, but never detach an unrelated transaction's file.
            if row.status == "deleting":
                return
            fail("找不到這筆交易的附件。", "not_found", 404)
        db.delete(link)
        receipt = db.scalar(
            select(Receipt).where(Receipt.owner_id == owner, Receipt.transaction_id == txn_id)
        )
        if receipt:
            db.execute(
                delete(ReceiptAttachment).where(
                    ReceiptAttachment.receipt_id == receipt.id,
                    ReceiptAttachment.attachment_id == identity,
                )
            )
        db.flush()
        refs = db.scalar(
            select(func.count())
            .select_from(TransactionAttachment)
            .where(TransactionAttachment.attachment_id == identity)
        )
        other = db.scalar(
            select(func.count())
            .select_from(ReceiptAttachment)
            .where(ReceiptAttachment.attachment_id == identity)
        )
        if not refs and not other:
            row.status, row.deleted_at = "deleting", now()
        audit(db, owner, "attachment.remove", str(identity), request)


def collect_garbage() -> int:
    """Crash leftovers and unreferenced deletions only; shared backup lock fences all FS writes."""
    removed = 0
    cutoff = now() - timedelta(hours=24)
    with transaction() as db:
        file_lock(db)
        root = root_path()
        for folder in (root / "staging", root / "objects"):
            if folder.is_symlink():
                raise ValueError("attachment_storage_invalid")
            if not folder.exists():
                continue
            for item in folder.iterdir():
                if (
                    item.is_symlink()
                    or not item.is_dir()
                    or item.stat().st_mtime >= cutoff.timestamp()
                ):
                    continue
                try:
                    key = uuid.UUID(item.name)
                    if str(key) != item.name:
                        continue
                except ValueError:
                    continue
                row = db.scalar(select(Attachment).where(Attachment.storage_key == key))
                if row:
                    if row.status != "deleting" or not row.deleted_at or row.deleted_at >= cutoff:
                        continue
                    refs = db.scalar(
                        select(func.count())
                        .select_from(TransactionAttachment)
                        .where(TransactionAttachment.attachment_id == row.id)
                    )
                    other = db.scalar(
                        select(func.count())
                        .select_from(ReceiptAttachment)
                        .where(ReceiptAttachment.attachment_id == row.id)
                    )
                    if refs or other:
                        continue
                shutil.rmtree(item)
                removed += 1
    return removed
