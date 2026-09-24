import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import Principal, audit, principal, require_csrf
from app.core.db import engine, transaction
from app.core.models import (
    Account,
    Category,
    FxQuote,
    JournalEntry,
    ReportSnapshot,
    Transaction,
    now,
)
from app.core.schemas import MessageOutput
from app.ledger import service as ledger
from app.ledger.reports import create_report, report_zip
from app.ledger.schemas import (
    AccountInput,
    AccountOutput,
    AccountUpdate,
    CategoryInput,
    CategoryOutput,
    QuoteInput,
    ReportInput,
    ReportOutput,
    TransactionInput,
    TransactionOutput,
    TransactionPage,
    TransactionUpdate,
    VoidInput,
)


def protect_writes(request: Request) -> None:
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        require_csrf(request)


router = APIRouter(prefix="/api/v1", dependencies=[Depends(protect_writes)])


@router.get("/accounts", response_model=list[AccountOutput])
def accounts(user: Principal = Depends(principal)) -> list[AccountOutput]:
    with Session(engine()) as db:
        return ledger.balance_accounts(db, user.owner_id)


@router.post("/accounts", response_model=AccountOutput)
def account_create(
    data: AccountInput,
    request: Request,
    key: str = Header(alias="Idempotency-Key"),
    user: Principal = Depends(principal),
) -> AccountOutput:
    with transaction() as db:
        identity = ledger.create_account(db, user.owner_id, data, key)
        audit(db, user.owner_id, "account.create", str(identity), request)
        return next(a for a in ledger.balance_accounts(db, user.owner_id) if a.id == identity)


@router.put("/accounts/{identity}", response_model=AccountOutput)
def account_update(
    identity: uuid.UUID, data: AccountUpdate, request: Request, user: Principal = Depends(principal)
) -> AccountOutput:
    with transaction() as db:
        book = ledger.book_lock(db, user.owner_id)
        account = ledger.owned(db, Account, user.owner_id, identity)
        if account.revision != data.expected_revision:
            ledger.fail("帳戶已變更，請重新載入。", "revision_conflict", 409)
        if not data.name.strip():
            ledger.fail("請填寫帳戶名稱。")
        account.name, account.include_in_net_worth = data.name.strip(), data.include_in_net_worth
        account.archived_at = now() if data.archived else None
        account.revision += 1
        book.ledger_revision += 1
        db.flush()
        audit(db, user.owner_id, "account.update", str(identity), request)
        return next(a for a in ledger.balance_accounts(db, user.owner_id) if a.id == identity)


@router.get("/categories", response_model=list[CategoryOutput])
def categories(user: Principal = Depends(principal)) -> list[CategoryOutput]:
    with Session(engine()) as db:
        return [
            CategoryOutput(
                id=c.id,
                name=c.name,
                kind=c.kind,
                parent_id=c.parent_id,
                archived=c.archived_at is not None,
            )
            for c in db.scalars(
                select(Category)
                .where(Category.owner_id == user.owner_id)
                .order_by(Category.created_at, Category.id)
            )
        ]


@router.post("/categories", response_model=CategoryOutput)
def category_create(
    data: CategoryInput,
    request: Request,
    key: str = Header(alias="Idempotency-Key"),
    user: Principal = Depends(principal),
) -> CategoryOutput:
    with transaction() as db:
        book = ledger.book_lock(db, user.owner_id)
        previous, digest = ledger.idempotent(db, user.owner_id, "category", key, data)
        if previous:
            row = ledger.owned(db, Category, user.owner_id, previous)
        else:
            if not data.name.strip():
                ledger.fail("請填寫分類名稱。")
            if data.parent_id:
                parent = ledger.validate_category(db, user.owner_id, data.parent_id, data.kind)
                if parent.parent_id:
                    ledger.fail("分類最多兩層。")
            row = Category(
                owner_id=user.owner_id,
                name=data.name.strip(),
                kind=data.kind,
                parent_id=data.parent_id,
            )
            db.add(row)
            db.flush()
            book.ledger_revision += 1
            ledger.remember(db, user.owner_id, "category", key, digest, row.id)
            audit(db, user.owner_id, "category.create", str(row.id), request)
        return CategoryOutput(id=row.id, name=row.name, kind=row.kind, parent_id=row.parent_id)


@router.get("/transactions", response_model=TransactionPage)
def transactions(
    start: date | None = None,
    end_exclusive: date | None = None,
    account_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    query: str = Query(default="", max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    user: Principal = Depends(principal),
) -> TransactionPage:
    with Session(engine()) as db:
        stmt = ledger.select_transactions(
            user.owner_id, start, end_exclusive, account_id, category_id, query
        )
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(
            db.scalars(
                stmt.order_by(
                    JournalEntry.occurred_on.desc(), Transaction.created_at.desc(), Transaction.id
                )
                .offset(offset)
                .limit(limit)
            )
        )
        return TransactionPage(
            items=ledger.transaction_outputs(db, user.owner_id, rows), total=total
        )


@router.post("/transactions", response_model=TransactionOutput)
def transaction_create(
    data: TransactionInput,
    request: Request,
    key: str = Header(alias="Idempotency-Key"),
    user: Principal = Depends(principal),
) -> TransactionOutput:
    with transaction() as db:
        identity = ledger.create_transaction(db, user.owner_id, data, key)
        audit(db, user.owner_id, "transaction.create", str(identity), request)
        if ledger.owned(db, Transaction, user.owner_id, identity).status == "voided":
            ledger.fail("這次提交的交易已作廢，請重新載入。", "resource_voided", 409)
        return ledger.transaction_outputs(
            db, user.owner_id, [ledger.owned(db, Transaction, user.owner_id, identity)]
        )[0]


@router.put("/transactions/{identity}", response_model=TransactionOutput)
def transaction_update(
    identity: uuid.UUID,
    data: TransactionUpdate,
    request: Request,
    key: str = Header(alias="Idempotency-Key"),
    user: Principal = Depends(principal),
) -> TransactionOutput:
    with transaction() as db:
        book = ledger.book_lock(db, user.owner_id)
        txn = ledger.owned(db, Transaction, user.owner_id, identity)
        operation = "transaction.update:" + str(identity)
        previous, digest = ledger.idempotent(db, user.owner_id, operation, key, data)
        if not previous:
            ledger.reversible(db, txn, data.expected_revision)
            if txn.kind != data.kind or txn.refund_of_id != data.refund_of_id:
                ledger.fail("更正時請保留交易類型與退款來源；需要更換時請作廢後新增。")
            txn.revision += 1
            ledger.reverse(db, txn, data.reason)
            ledger.write_entry(db, user.owner_id, book, txn, data, "replacement", data.reason)
            ledger.remember(db, user.owner_id, operation, key, digest, identity)
            audit(db, user.owner_id, "transaction.update", str(identity), request)
        return ledger.transaction_outputs(db, user.owner_id, [txn])[0]


@router.post("/transactions/{identity}/void", response_model=MessageOutput)
def transaction_void(
    identity: uuid.UUID,
    data: VoidInput,
    request: Request,
    key: str = Header(alias="Idempotency-Key"),
    user: Principal = Depends(principal),
) -> MessageOutput:
    with transaction() as db:
        book = ledger.book_lock(db, user.owner_id)
        txn = ledger.owned(db, Transaction, user.owner_id, identity)
        operation = "transaction.void:" + str(identity)
        previous, digest = ledger.idempotent(db, user.owner_id, operation, key, data)
        if not previous:
            ledger.reversible(db, txn, data.expected_revision)
            txn.revision += 1
            ledger.reverse(db, txn, data.reason)
            txn.current_entry_id, txn.status = None, "voided"
            book.ledger_revision += 1
            ledger.remember(db, user.owner_id, operation, key, digest, identity)
            audit(db, user.owner_id, "transaction.void", str(identity), request)
        return MessageOutput(message="交易已作廢，原始紀錄已保留。")


@router.post("/fx-quotes", response_model=MessageOutput)
def quote_create(
    data: QuoteInput,
    request: Request,
    key: str = Header(alias="Idempotency-Key"),
    user: Principal = Depends(principal),
) -> MessageOutput:
    with transaction() as db:
        book = ledger.book_lock(db, user.owner_id)
        assert book.book_currency
        previous, digest = ledger.idempotent(db, user.owner_id, "quote", key, data)
        if not previous:
            ledger.rate(data.currency, book.book_currency, data.rate)
            row = FxQuote(
                owner_id=user.owner_id,
                currency=data.currency,
                book_currency=book.book_currency,
                effective_on=data.effective_on,
                rate=Decimal(data.rate),
                source=data.source,
            )
            db.add(row)
            db.flush()
            book.valuation_revision += 1
            ledger.remember(db, user.owner_id, "quote", key, digest, row.id)
            audit(db, user.owner_id, "quote.create", str(row.id), request)
        return MessageOutput(message="估值匯率已保存。歷史交易入帳金額保持原紀錄。")


@router.post("/reports", response_model=ReportOutput)
def report_create(params: ReportInput, user: Principal = Depends(principal)) -> ReportOutput:
    with transaction() as db:
        snapshot = create_report(db, user.owner_id, params)
        return ReportOutput(id=snapshot.id, document=snapshot.document)


@router.get("/reports/{identity}", response_model=ReportOutput)
def report_read(identity: uuid.UUID, user: Principal = Depends(principal)) -> ReportOutput:
    with Session(engine()) as db:
        snapshot = ledger.owned(db, ReportSnapshot, user.owner_id, identity)
        return ReportOutput(id=snapshot.id, document=snapshot.document)


@router.get("/reports/{identity}/csv")
def report_download(identity: uuid.UUID, user: Principal = Depends(principal)) -> Response:
    with Session(engine()) as db:
        snapshot = ledger.owned(db, ReportSnapshot, user.owner_id, identity)
        return Response(
            report_zip(snapshot),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="finance-report-{identity}.zip"'
            },
        )
