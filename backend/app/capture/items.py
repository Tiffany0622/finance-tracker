"""Human-reviewed item history, independent of the immutable financial journal."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.auth import Principal, audit, principal
from app.core.db import engine, transaction
from app.core.models import CaptureDraft, JournalEntry, ReceiptItem, ReceiptItemReview, Transaction
from app.core.schemas import StrictModel
from app.ledger.routes import protect_writes
from app.ledger.schemas import Money
from app.ledger.service import book_lock, fail, number, owned, transaction_outputs


class ItemInput(StrictModel):
    source_line_no: int | None = Field(default=None, ge=1, le=200)
    name: str = Field(min_length=1, max_length=500)
    quantity: Money | None = None
    unit_price: Money | None = None
    line_total: Money | None = None
    unit: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("品名不可空白")
        return value.strip()

    @model_validator(mode="after")
    def positive(self) -> "ItemInput":
        if self.quantity is not None and Decimal(self.quantity) <= 0:
            raise ValueError("數量須大於零；不明請留白")
        if self.unit_price is not None and Decimal(self.unit_price) < 0:
            raise ValueError("單價不可為負；折扣可填負的列金額")
        return self


class ItemOutput(BaseModel):
    source_line_no: int | None
    raw_name: str
    name: str
    quantity: str | None
    unit_price: str | None
    line_total: str | None
    unit: str
    note: str


class ReviewInput(StrictModel):
    expected_revision: int = Field(ge=0)
    expected_draft_revision: int = Field(ge=1)
    expected_transaction_revision: int | None = Field(default=None, ge=1)
    acknowledged: Literal[True]
    items: list[ItemInput] = Field(max_length=200)


class ReviewOutput(BaseModel):
    revision: int
    draft_revision: int
    transaction_revision: int | None
    currency: str | None
    status: Literal["unreviewed", "reviewed", "stale"]
    editable: bool
    items: list[ItemOutput]
    warnings: list[str]


class PurchaseOutput(ItemOutput):
    draft_id: uuid.UUID
    transaction_id: uuid.UUID
    occurred_on: date
    merchant: str
    currency: str


class SearchOutput(BaseModel):
    items: list[PurchaseOutput]
    has_more: bool
    pending_receipts: int


def latest_review(db: Session, row: CaptureDraft) -> ReceiptItemReview | None:
    return db.scalar(
        select(ReceiptItemReview)
        .where(ReceiptItemReview.draft_id == row.id, ReceiptItemReview.owner_id == row.owner_id)
        .order_by(ReceiptItemReview.revision.desc())
        .limit(1)
    )


def review_lines(db: Session, review: ReceiptItemReview) -> list[ItemOutput]:
    return [
        item_output(line)
        for line in db.scalars(
            select(ReceiptItem)
            .where(ReceiptItem.review_id == review.id, ReceiptItem.owner_id == review.owner_id)
            .order_by(ReceiptItem.line_no)
        )
    ]


def item_output(line: ReceiptItem) -> ItemOutput:
    return ItemOutput(
        source_line_no=line.source_line_no,
        raw_name=line.raw_name,
        name=line.name,
        quantity=number(line.quantity) if line.quantity is not None else None,
        unit_price=number(line.unit_price) if line.unit_price is not None else None,
        line_total=number(line.line_total) if line.line_total is not None else None,
        unit=line.unit,
        note=line.note,
    )


def context(db: Session, row: CaptureDraft) -> tuple[int | None, str | None, bool]:
    if row.confirmed_transaction_id:
        txn = owned(db, Transaction, row.owner_id, row.confirmed_transaction_id)
        if not txn.current_entry_id or txn.status != "posted":
            return txn.revision, row.proposal.get("currency"), False
        output = transaction_outputs(db, row.owner_id, [txn])[0]
        return txn.revision, output.currency, txn.status == "posted" and txn.kind == "expense"
    return (
        None,
        row.proposal.get("currency"),
        row.status not in {"processing", "cancelled"}
        and row.proposal.get("kind", "expense") == "expense",
    )


def warnings(items: list[ItemOutput], parsed: dict[str, Any] | None) -> list[str]:
    result = []
    if not items:
        result.append("沒有商品明細，不會新增歷史商品紀錄。")
    if any(i.quantity is None or i.unit_price is None or i.line_total is None for i in items):
        result.append("部分數量或價格不明；留白不會當成零，也不會用整筆支出推算。")
    for n, i in enumerate(items, 1):
        if i.quantity is not None and i.unit_price is not None and i.line_total is not None:
            if abs(Decimal(i.quantity) * Decimal(i.unit_price) - Decimal(i.line_total)) > Decimal(
                "0.01"
            ):
                result.append(f"第 {n} 項：數量 × 單價與列金額不同，請核對折扣或收據原文。")
    if (
        items
        and all(i.line_total is not None for i in items)
        and parsed
        and parsed.get("subtotal") is not None
    ):
        total = sum((Decimal(i.line_total) for i in items if i.line_total is not None), Decimal(0))
        if abs(total - Decimal(parsed["subtotal"])) > Decimal("0.01"):
            result.append("明細合計與 AI 辨識小計不同，請確認是否有漏項或折扣；不會改動帳務總額。")
    return result


def review_output(db: Session, row: CaptureDraft) -> ReviewOutput:
    review = latest_review(db, row)
    tx_revision, currency, can_edit = context(db, row)
    status: Literal["unreviewed", "reviewed", "stale"] = "unreviewed"
    if review:
        items = review_lines(db, review)
        status = (
            "reviewed"
            if review.draft_revision == row.revision and review.transaction_revision == tx_revision
            else "stale"
        )
    else:
        items = [
            ItemOutput(
                source_line_no=n,
                name=i["raw_name"],
                raw_name=i["raw_name"],
                unit="",
                note="",
                quantity=i.get("quantity"),
                unit_price=i.get("unit_price"),
                line_total=i.get("line_total"),
            )
            for n, i in enumerate((row.parsed or {}).get("items", []), 1)
        ]
    notes = warnings(items, row.parsed)
    if status == "stale":
        notes.insert(0, "草稿、辨識或帳務已變動。保留上次修正，請重新核對後儲存。")
    return ReviewOutput(
        revision=review.revision if review else 0,
        draft_revision=row.revision,
        transaction_revision=tx_revision,
        currency=currency,
        status=status,
        editable=can_edit,
        items=items,
        warnings=notes,
    )


def append_review(
    db: Session,
    row: CaptureDraft,
    revision: int,
    tx_revision: int | None,
    currency: str,
    lines: list[ItemOutput],
    parsed: dict[str, Any] | None,
) -> ReceiptItemReview:
    review = ReceiptItemReview(
        owner_id=row.owner_id,
        draft_id=row.id,
        revision=revision,
        draft_revision=row.revision,
        transaction_revision=tx_revision,
        currency=currency,
        parsed_snapshot=parsed,
    )
    db.add(review)
    db.flush()
    for n, item in enumerate(lines, 1):
        values = item.model_dump()
        for key in ("quantity", "unit_price", "line_total"):
            values[key] = Decimal(values[key]) if values[key] is not None else None
        db.add(ReceiptItem(owner_id=row.owner_id, review_id=review.id, line_no=n, **values))
    db.flush()
    return review


def save_review(
    db: Session, owner: uuid.UUID, identity: uuid.UUID, body: ReviewInput, request: Request
) -> ReviewOutput:
    book_lock(db, owner)
    row = owned(db, CaptureDraft, owner, identity)
    current = review_output(db, row)
    if (
        body.expected_revision,
        body.expected_draft_revision,
        body.expected_transaction_revision,
    ) != (current.revision, current.draft_revision, current.transaction_revision):
        fail("品項或帳務已更新，請重新載入後核對。", "item_revision_conflict", 409)
    if not current.editable:
        fail("處理中、已取消或非有效支出的收據不能核對品項。", "items_not_editable", 409)
    if current.currency not in {"USD", "TWD"}:
        fail("請先核對幣別並儲存草稿修改。", "item_currency_required", 422)
    # Unchanged source links refer to the snapshot accompanying the existing corrections.
    # A retry never silently reassigns a corrected line to a different raw item.
    previous = latest_review(db, row)
    raw = (previous.parsed_snapshot if previous else row.parsed) or {}
    source = raw.get("items", [])
    lines = []
    for item in body.items:
        if item.source_line_no and item.source_line_no > len(source):
            fail("原始品項連結無效，請重新載入。", "item_source_invalid", 422)
        raw_name = source[item.source_line_no - 1]["raw_name"] if item.source_line_no else ""
        lines.append(ItemOutput(**item.model_dump(), raw_name=raw_name))
    review = append_review(
        db, row, current.revision + 1, current.transaction_revision, current.currency, lines, raw
    )
    audit(db, owner, "receipt.items.review", str(review.id), request)
    return review_output(db, row)


def carry_review_to_posted(db: Session, row: CaptureDraft, previous_revision: int) -> None:
    """Only carry an already reviewed, unchanged draft through explicit confirmation."""
    review = latest_review(db, row)
    if (
        review
        and review.draft_revision == previous_revision
        and review.transaction_revision is None
    ):
        tx_revision, currency, valid = context(db, row)
        if valid and currency == review.currency:
            append_review(
                db,
                row,
                review.revision + 1,
                tx_revision,
                currency,
                review_lines(db, review),
                review.parsed_snapshot,
            )


def search(db: Session, owner: uuid.UUID, query: str, offset: int, limit: int = 25) -> SearchOutput:
    r, d, t, j = ReceiptItemReview, CaptureDraft, Transaction, JournalEntry
    newer = aliased(ReceiptItemReview)
    latest = (
        ~select(newer.id).where(newer.draft_id == r.draft_id, newer.revision > r.revision).exists()
    )
    valid = and_(r.draft_revision == d.revision, r.transaction_revision == t.revision, latest)
    base = (
        select(d.id)
        .join(t, t.id == d.confirmed_transaction_id)
        .where(
            d.owner_id == owner,
            d.status == "confirmed",
            t.owner_id == owner,
            t.kind == "expense",
            t.status == "posted",
        )
    )
    pending = (
        db.scalar(
            select(func.count()).select_from(
                base.outerjoin(r, and_(r.draft_id == d.id, valid)).where(r.id.is_(None)).subquery()
            )
        )
        or 0
    )
    # Literal substring search: '%' and '_' are characters, not SQL wildcards.
    literal = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    q = (
        select(ReceiptItem, d.id, t.id, j.occurred_on, t.merchant, r.currency)
        .join(r, r.id == ReceiptItem.review_id)
        .join(d, d.id == r.draft_id)
        .join(t, t.id == d.confirmed_transaction_id)
        .join(j, j.id == t.current_entry_id)
        .where(
            ReceiptItem.owner_id == owner,
            r.owner_id == owner,
            d.owner_id == owner,
            t.owner_id == owner,
            d.status == "confirmed",
            t.kind == "expense",
            t.status == "posted",
            valid,
            or_(
                ReceiptItem.name.ilike(f"%{literal}%", escape="\\"),
                ReceiptItem.raw_name.ilike(f"%{literal}%", escape="\\"),
            ),
        )
        .order_by(j.occurred_on.desc(), t.id, ReceiptItem.line_no)
        .offset(offset)
        .limit(limit + 1)
    )
    rows = list(db.execute(q))
    return SearchOutput(
        items=[
            PurchaseOutput(
                **item_output(i).model_dump(),
                draft_id=did,
                transaction_id=tid,
                occurred_on=day,
                merchant=merchant,
                currency=currency,
            )
            for i, did, tid, day, merchant, currency in rows[:limit]
        ],
        has_more=len(rows) > limit,
        pending_receipts=pending,
    )


router = APIRouter(prefix="/api/v1", dependencies=[Depends(protect_writes)])


@router.get("/capture/drafts/{identity}/items", response_model=ReviewOutput)
def get_items(identity: uuid.UUID, user: Principal = Depends(principal)) -> ReviewOutput:
    with Session(engine()) as db:
        return review_output(db, owned(db, CaptureDraft, user.owner_id, identity))


@router.put("/capture/drafts/{identity}/items", response_model=ReviewOutput)
def put_items(
    identity: uuid.UUID, body: ReviewInput, request: Request, user: Principal = Depends(principal)
) -> ReviewOutput:
    with transaction() as db:
        return save_review(db, user.owner_id, identity, body, request)


@router.get("/products/history", response_model=SearchOutput)
def history(
    q: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0, le=100000),
    user: Principal = Depends(principal),
) -> SearchOutput:
    with Session(engine()) as db:
        return search(db, user.owner_id, q, offset)
