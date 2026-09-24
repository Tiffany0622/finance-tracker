"""Owner-scoped, balanced, append-only journal. Call inside transaction()."""

import hashlib
import json
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any, NoReturn

from pydantic import BaseModel
from sqlalchemy import Text, cast, func, select
from sqlalchemy.orm import Session

from app.core.auth import ApiError
from app.core.models import (
    Account,
    BookSettings,
    Category,
    Currency,
    IdempotencyRecord,
    JournalEntry,
    LedgerAccount,
    Posting,
    Transaction,
    TransactionSplit,
)
from app.ledger.schemas import (
    AccountInput,
    AccountOutput,
    SplitInput,
    TransactionInput,
    TransactionOutput,
)

ZERO = Decimal(0)


def number(value: Decimal) -> str:
    return format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else str(value)


def fail(message: str, code: str = "invalid_ledger", status: int = 422) -> NoReturn:
    raise ApiError(status, code, message)


def book_lock(db: Session, owner: uuid.UUID) -> BookSettings:
    book = db.scalar(select(BookSettings).where(BookSettings.owner_id == owner).with_for_update())
    if not book or not book.setup_completed_at or not book.book_currency:
        raise ApiError(409, "setup_required", "請先在偏好設定完成幣別與時區。")
    return book


def owned(db: Session, cls: Any, owner: uuid.UUID, identity: uuid.UUID) -> Any:
    obj = db.get(cls, identity)
    if obj is None or obj.owner_id != owner:
        raise ApiError(404, "not_found", "找不到此資料。")
    return obj


def money(db: Session, value: str | Decimal, currency: str) -> Decimal:
    amount = Decimal(value)
    scale = db.get(Currency, currency)
    if scale is None:
        raise ApiError(422, "currency", "不支援此幣別。")
    if not amount.is_finite() or abs(amount) >= Decimal("1e14"):
        fail("金額超出可接受範圍。")
    if amount != amount.quantize(Decimal(1).scaleb(-scale.amount_scale)):
        fail(f"{currency} 金額最多 {scale.amount_scale} 位小數。")
    return amount


def rate(currency: str, book: str, value: str | None) -> Decimal:
    if currency == book:
        if value is not None and Decimal(value) != 1:
            fail("基準幣別匯率必須為 1。")
        return Decimal(1)
    if value is None or Decimal(value) <= 0 or Decimal(value) >= Decimal("1e10"):
        fail("外幣入帳需填寫有效匯率：1 單位原幣可換多少基準幣別。", "fx_required")
    return Decimal(value)


def convert(db: Session, amount: Decimal, fx: Decimal, book: str) -> Decimal:
    currency = db.get(Currency, book)
    assert currency
    with localcontext() as ctx:
        ctx.prec = 60
        value = (amount * fx).quantize(
            Decimal(1).scaleb(-currency.amount_scale), rounding=ROUND_HALF_UP
        )
    if abs(value) >= Decimal("1e18"):
        fail("換算金額超出可接受範圍。")
    return value


def idempotent(
    db: Session, owner: uuid.UUID, operation: str, key: str, data: BaseModel
) -> tuple[uuid.UUID | None, str]:
    if not key or len(key) > 100:
        fail("缺少有效的提交識別碼。", "idempotency_required")
    digest = hashlib.sha256(
        json.dumps(data.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    previous = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.owner_id == owner,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.key == key,
        )
    )
    if previous:
        if previous.request_hash != digest:
            fail("此提交識別碼已用於其他內容，請重新載入。", "idempotency_conflict", 409)
        return previous.resource_id, digest
    return None, digest


def remember(
    db: Session, owner: uuid.UUID, operation: str, key: str, digest: str, identity: uuid.UUID
) -> None:
    db.add(
        IdempotencyRecord(
            owner_id=owner,
            operation=operation,
            key=key,
            request_hash=digest,
            resource_id=identity,
            response_code=200,
        )
    )


def ledger(
    db: Session, owner: uuid.UUID, currency: str, role: str, account: Account | None = None
) -> LedgerAccount:
    code = f"account:{account.id}" if account else f"{role}:{currency}"
    row = db.scalar(
        select(LedgerAccount).where(LedgerAccount.owner_id == owner, LedgerAccount.code == code)
    )
    if row is None:
        row = LedgerAccount(
            owner_id=owner,
            code=code,
            account_id=account.id if account else None,
            currency=currency,
            ledger_class=role,
        )
        db.add(row)
        db.flush()
    return row


def active_account(db: Session, owner: uuid.UUID, identity: uuid.UUID) -> Account:
    account: Account = owned(db, Account, owner, identity)
    if account.archived_at:
        fail("此帳戶已封存，請先恢復帳戶。")
    return account


def entry(
    db: Session,
    owner: uuid.UUID,
    txn: Transaction,
    occurred: date,
    book: str,
    role: str = "original",
    reason: str = "",
    reverses: uuid.UUID | None = None,
) -> JournalEntry:
    row = JournalEntry(
        owner_id=owner,
        transaction_id=txn.id,
        revision_no=txn.revision,
        entry_role=role,
        occurred_on=occurred,
        book_currency=book,
        reason=reason,
        reverses_entry_id=reverses,
    )
    db.add(row)
    db.flush()
    return row


def post(
    db: Session,
    journal: JournalEntry,
    account: LedgerAccount,
    amount: Decimal,
    fx: Decimal,
    component: str,
    book_amount: Decimal | None = None,
) -> Posting:
    line = (
        db.scalar(select(func.max(Posting.line_no)).where(Posting.entry_id == journal.id)) or 0
    ) + 1
    row = Posting(
        owner_id=journal.owner_id,
        entry_id=journal.id,
        line_no=line,
        ledger_account_id=account.id,
        currency=account.currency,
        amount_signed=amount,
        fx_rate=fx,
        component=component,
        book_amount_signed=convert(db, amount, fx, journal.book_currency)
        if book_amount is None
        else book_amount,
    )
    db.add(row)
    db.flush()
    return row


def balance_accounts(
    db: Session, owner: uuid.UUID, before: date | None = None
) -> list[AccountOutput]:
    totals = (
        select(LedgerAccount.account_id, func.sum(Posting.amount_signed).label("balance"))
        .join(Posting, Posting.ledger_account_id == LedgerAccount.id)
        .join(JournalEntry, JournalEntry.id == Posting.entry_id)
        .where(Posting.owner_id == owner)
    )
    if before:
        totals = totals.where(JournalEntry.occurred_on < before)
    sums = {
        identity: value for identity, value in db.execute(totals.group_by(LedgerAccount.account_id))
    }
    return [
        AccountOutput(
            id=a.id,
            name=a.name,
            kind=a.kind,
            currency=a.currency,
            balance=number(sums.get(a.id, ZERO) * (-1 if a.kind == "credit_card" else 1)),
            include_in_net_worth=a.include_in_net_worth,
            archived=a.archived_at is not None,
            revision=a.revision,
        )
        for a in db.scalars(
            select(Account)
            .where(Account.owner_id == owner)
            .order_by(Account.created_at, Account.id)
        )
    ]


def create_account(db: Session, owner: uuid.UUID, data: AccountInput, key: str) -> uuid.UUID:
    book = book_lock(db, owner)
    previous, digest = idempotent(db, owner, "account", key, data)
    if previous:
        return previous
    name = data.name.strip()
    if not name:
        fail("請填寫帳戶名稱。")
    amount = money(db, data.opening_balance, data.currency)
    account = Account(
        owner_id=owner,
        name=name,
        kind=data.kind,
        currency=data.currency,
        include_in_net_worth=data.include_in_net_worth,
    )
    db.add(account)
    db.flush()
    target = ledger(
        db, owner, data.currency, "liability" if data.kind == "credit_card" else "asset", account
    )
    if amount:
        assert book.book_currency
        fx = rate(data.currency, book.book_currency, data.fx_rate)
        txn = Transaction(owner_id=owner, kind="opening", note="期初餘額")
        db.add(txn)
        db.flush()
        journal = entry(db, owner, txn, data.opening_on, book.book_currency)
        signed = -amount if data.kind == "credit_card" else amount
        post(db, journal, target, signed, fx, "opening")
        post(db, journal, ledger(db, owner, data.currency, "equity"), -signed, fx, "opening")
        txn.current_entry_id = journal.id
    book.ledger_revision += 1
    remember(db, owner, "account", key, digest, account.id)
    return account.id


def validate_category(db: Session, owner: uuid.UUID, identity: uuid.UUID, kind: str) -> Category:
    category: Category = owned(db, Category, owner, identity)
    if category.archived_at or category.kind != kind:
        fail("請選擇符合收支類型且未封存的分類。")
    return category


def add_splits(
    db: Session, journal: JournalEntry, posting: Posting, values: list[tuple[uuid.UUID, Decimal]]
) -> None:
    # Deterministically assign conversion rounding remainder to the final split.
    remaining = posting.book_amount_signed
    for index, (category, amount) in enumerate(values):
        converted = (
            remaining
            if index == len(values) - 1
            else convert(db, amount, posting.fx_rate, journal.book_currency)
        )
        remaining -= converted
        db.add(
            TransactionSplit(
                owner_id=journal.owner_id,
                entry_id=journal.id,
                posting_id=posting.id,
                category_id=category,
                amount_signed=amount,
                book_amount_signed=converted,
            )
        )
    db.flush()


def refund_splits(
    db: Session,
    owner: uuid.UUID,
    original: Transaction,
    amount: Decimal,
    currency: str,
    exclude: uuid.UUID,
) -> list[tuple[uuid.UUID, Decimal]]:
    if original.kind != "expense" or original.status != "posted":
        fail("只能對有效的支出辦理退款。")
    source = db.execute(
        select(TransactionSplit.category_id, TransactionSplit.amount_signed, Posting.currency)
        .join(Posting, Posting.id == TransactionSplit.posting_id)
        .where(TransactionSplit.entry_id == original.current_entry_id)
    ).all()
    if not source or any(row.currency != currency for row in source):
        fail("本版退款須與原交易使用相同幣別。")
    originals: dict[uuid.UUID, Decimal] = {}
    for category, value, _ in source:
        originals[category] = originals.get(category, ZERO) + value
    refunds = db.execute(
        select(TransactionSplit.category_id, func.sum(TransactionSplit.amount_signed))
        .join(Transaction, Transaction.current_entry_id == TransactionSplit.entry_id)
        .where(
            Transaction.refund_of_id == original.id,
            Transaction.status == "posted",
            Transaction.id != exclude,
        )
        .group_by(TransactionSplit.category_id)
    ).all()
    for category, value in refunds:
        originals[category] += value
    total = sum(originals.values(), ZERO)
    if amount > total:
        fail("退款金額超過原交易的剩餘可退金額。", "refund_exceeded", 409)
    remaining = amount
    values = []
    # Round down proportional shares, then distribute leftover minor units without exceeding caps.
    scale = db.get(Currency, currency)
    assert scale
    unit = Decimal(1).scaleb(-scale.amount_scale)
    available = sorted(originals.items(), key=lambda row: str(row[0]))
    shares = {cat: (amount * cap / total // unit) * unit for cat, cap in available}
    left = amount - sum(shares.values(), ZERO)
    for cat, cap in available:
        extra = min(left, cap - shares[cat])
        shares[cat] += extra
        left -= extra
    for cat, _ in available:
        if shares[cat]:
            values.append((cat, -shares[cat]))
            remaining -= shares[cat]
    assert remaining == 0
    return values


def write_entry(
    db: Session,
    owner: uuid.UUID,
    book: BookSettings,
    txn: Transaction,
    data: TransactionInput,
    role: str = "original",
    reason: str = "",
) -> None:
    assert book.book_currency
    account = active_account(db, owner, data.account_id)
    amount = money(db, data.amount, account.currency)
    fx = rate(account.currency, book.book_currency, data.fx_rate)
    target = ledger(
        db,
        owner,
        account.currency,
        "liability" if account.kind == "credit_card" else "asset",
        account,
    )
    journal = entry(db, owner, txn, data.occurred_on, book.book_currency, role, reason)
    if data.kind == "transfer":
        if (
            not data.to_account_id
            or data.to_account_id == account.id
            or data.category_id
            or data.splits
        ):
            fail("轉帳需選擇兩個不同帳戶，本金不使用收支分類。")
        destination = active_account(db, owner, data.to_account_id)
        incoming = money(db, data.received_amount or data.amount, destination.currency)
        if incoming <= 0 or (destination.currency == account.currency and incoming != amount):
            fail("同幣轉帳的本金必須一致；轉入金額必須大於零。")
        if destination.currency != account.currency and data.received_amount is None:
            fail("跨幣轉帳須填寫實際入帳金額。")
        to_fx = rate(
            destination.currency,
            book.book_currency,
            data.received_fx_rate if destination.currency != account.currency else data.fx_rate,
        )
        outgoing_post = post(db, journal, target, -amount, fx, "principal")
        incoming_post = post(
            db,
            journal,
            ledger(
                db,
                owner,
                destination.currency,
                "liability" if destination.kind == "credit_card" else "asset",
                destination,
            ),
            incoming,
            to_fx,
            "principal",
        )
        bridge = -(outgoing_post.book_amount_signed + incoming_post.book_amount_signed)
        if bridge:
            post(
                db,
                journal,
                ledger(db, owner, book.book_currency, "equity"),
                bridge,
                Decimal(1),
                "fx",
            )
        fee = money(db, data.fee, account.currency)
        if fee:
            if not data.fee_category_id:
                fail("請選擇手續費分類。")
            validate_category(db, owner, data.fee_category_id, "expense")
            post(db, journal, target, -fee, fx, "fee")
            charge = post(
                db, journal, ledger(db, owner, account.currency, "expense"), fee, fx, "fee"
            )
            add_splits(db, journal, charge, [(data.fee_category_id, fee)])
    else:
        if data.kind == "refund":
            if not data.refund_of_id or data.category_id or data.splits:
                fail("退款須關聯原支出，分類沿用原交易。")
            original: Transaction = owned(db, Transaction, owner, data.refund_of_id)
            values = refund_splits(db, owner, original, amount, account.currency, txn.id)
            signed = -amount
            counter_class = "expense"
        else:
            if bool(data.category_id) == bool(data.splits):
                fail("請選擇一個分類或提供完整分攤。")
            if data.splits:
                values = [
                    (s.category_id, money(db, s.amount, account.currency)) for s in data.splits
                ]
            else:
                assert data.category_id
                values = [(data.category_id, amount)]
            if any(v <= 0 for _, v in values) or sum((v for _, v in values), ZERO) != amount:
                fail("分類分攤必須為正且合計等於交易金額。")
            for category, _ in values:
                assert category
                validate_category(db, owner, category, data.kind)
            signed = amount if data.kind == "expense" else -amount
            if data.kind == "income":
                values = [(cat, -value) for cat, value in values]
            counter_class = data.kind
        post(db, journal, target, -signed, fx, "principal")
        counter = post(
            db,
            journal,
            ledger(db, owner, account.currency, counter_class),
            signed,
            fx,
            counter_class,
        )
        add_splits(db, journal, counter, values)  # type: ignore[arg-type]
    txn.current_entry_id = journal.id
    txn.note, txn.merchant, txn.tags = data.note.strip(), data.merchant.strip(), data.tags
    txn.refund_of_id = data.refund_of_id
    book.ledger_revision += 1
    db.flush()


def create_transaction(
    db: Session, owner: uuid.UUID, data: TransactionInput, key: str
) -> uuid.UUID:
    book = book_lock(db, owner)
    previous, digest = idempotent(db, owner, "transaction", key, data)
    if previous:
        return previous
    txn = Transaction(owner_id=owner, kind=data.kind)
    db.add(txn)
    db.flush()
    write_entry(db, owner, book, txn, data)
    remember(db, owner, "transaction", key, digest, txn.id)
    return txn.id


def reversible(db: Session, txn: Transaction, expected: int) -> None:
    if txn.revision != expected or txn.status != "posted":
        fail("這筆交易已變更，請重新載入後再操作。", "revision_conflict", 409)
    if txn.kind in ("opening", "adjustment"):
        fail("期初與調整分錄不可從一般交易編輯。")
    if db.scalar(
        select(Transaction.id)
        .where(Transaction.refund_of_id == txn.id, Transaction.status == "posted")
        .limit(1)
    ):
        fail("此交易已有退款，請先處理退款再更正原交易。", "refund_linked", 409)


def reverse(db: Session, txn: Transaction, reason: str) -> None:
    original = db.get(JournalEntry, txn.current_entry_id)
    assert original
    journal = entry(
        db,
        txn.owner_id,
        txn,
        original.occurred_on,
        original.book_currency,
        "reversal",
        reason,
        original.id,
    )
    for line in db.scalars(
        select(Posting).where(Posting.entry_id == original.id).order_by(Posting.line_no)
    ):
        target = db.get(LedgerAccount, line.ledger_account_id)
        assert target
        reversed_post = post(
            db,
            journal,
            target,
            -line.amount_signed,
            line.fx_rate,
            line.component,
            -line.book_amount_signed,
        )
        for split in db.scalars(
            select(TransactionSplit).where(TransactionSplit.posting_id == line.id)
        ):
            db.add(
                TransactionSplit(
                    owner_id=txn.owner_id,
                    entry_id=journal.id,
                    posting_id=reversed_post.id,
                    category_id=split.category_id,
                    amount_signed=-split.amount_signed,
                    book_amount_signed=-split.book_amount_signed,
                )
            )
    db.flush()


def transaction_outputs(
    db: Session, owner: uuid.UUID, txns: list[Transaction]
) -> list[TransactionOutput]:
    ids = [t.current_entry_id for t in txns if t.current_entry_id]
    if not ids:
        return []
    entries = {
        e.id: e
        for e in db.scalars(
            select(JournalEntry).where(JournalEntry.id.in_(ids), JournalEntry.owner_id == owner)
        )
    }
    lines: dict[uuid.UUID, list[Any]] = {}
    for p, la, a in db.execute(
        select(Posting, LedgerAccount, Account)
        .join(LedgerAccount, LedgerAccount.id == Posting.ledger_account_id)
        .outerjoin(Account, Account.id == LedgerAccount.account_id)
        .where(Posting.entry_id.in_(ids), Posting.owner_id == owner)
        .order_by(Posting.line_no)
    ):
        lines.setdefault(p.entry_id, []).append((p, la, a))
    splits: dict[uuid.UUID, list[Any]] = {}
    for split, cat in db.execute(
        select(TransactionSplit, Category)
        .join(Category, Category.id == TransactionSplit.category_id)
        .where(TransactionSplit.entry_id.in_(ids), TransactionSplit.owner_id == owner)
        .order_by(TransactionSplit.id)
    ):
        splits.setdefault(split.entry_id, []).append((split, cat))
    result = []
    for txn in txns:
        if not txn.current_entry_id:
            continue
        journal = entries[txn.current_entry_id]
        own_lines = [
            (p, a) for p, _, a in lines[journal.id] if a and p.component in ("principal", "opening")
        ]
        first, account = own_lines[0]
        to = own_lines[1] if txn.kind == "transfer" else None
        categorized = splits.get(journal.id, [])
        fee = next(((p, a) for p, _, a in lines[journal.id] if a and p.component == "fee"), None)
        result.append(
            TransactionOutput(
                id=txn.id,
                revision=txn.revision,
                status=txn.status,
                kind=txn.kind,
                occurred_on=journal.occurred_on,
                account_id=account.id,
                account=account.name,
                to_account_id=to[1].id if to else None,
                to_account=to[1].name if to else "",
                amount=number(abs(first.amount_signed)),
                currency=first.currency,
                book_amount=number(abs(first.book_amount_signed)),
                received_amount=number(to[0].amount_signed) if to else None,
                fx_rate=number(first.fx_rate),
                received_fx_rate=number(to[0].fx_rate) if to else None,
                fee=number(abs(fee[0].amount_signed)) if fee else "0",
                fee_category_id=categorized[0][1].id if fee and categorized else None,
                splits=[
                    SplitInput(category_id=cat.id, amount=number(abs(s.amount_signed)))
                    for s, cat in categorized
                ],
                categories=[cat.name for _, cat in categorized],
                refund_of_id=txn.refund_of_id,
                merchant=txn.merchant,
                note=txn.note,
                tags=txn.tags,
            )
        )
    return result


def select_transactions(
    owner: uuid.UUID,
    start: date | None = None,
    end: date | None = None,
    account_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    query: str = "",
) -> Any:
    stmt = (
        select(Transaction)
        .join(JournalEntry, JournalEntry.id == Transaction.current_entry_id)
        .where(Transaction.owner_id == owner, Transaction.status == "posted")
    )
    if start:
        stmt = stmt.where(JournalEntry.occurred_on >= start)
    if end:
        stmt = stmt.where(JournalEntry.occurred_on < end)
    if account_id:
        stmt = stmt.where(
            JournalEntry.id.in_(
                select(Posting.entry_id)
                .join(LedgerAccount, LedgerAccount.id == Posting.ledger_account_id)
                .where(LedgerAccount.account_id == account_id, Posting.owner_id == owner)
            )
        )
    if category_id:
        stmt = stmt.where(
            JournalEntry.id.in_(
                select(TransactionSplit.entry_id)
                .join(Category, Category.id == TransactionSplit.category_id)
                .where(
                    (Category.id == category_id) | (Category.parent_id == category_id),
                    TransactionSplit.owner_id == owner,
                )
            )
        )
    if query:
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(
            Transaction.note.ilike(pattern)
            | Transaction.merchant.ilike(pattern)
            | cast(Transaction.tags, Text).ilike(pattern)
        )
    return stmt
