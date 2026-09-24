import csv
import hashlib
import io
import json
import uuid
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.models import (
    Category,
    FxQuote,
    JournalEntry,
    ReportSnapshot,
    Transaction,
    TransactionSplit,
    now,
)
from app.ledger.schemas import ReportInput
from app.ledger.service import (
    ZERO,
    balance_accounts,
    book_lock,
    convert,
    number,
    owned,
    select_transactions,
    transaction_outputs,
)


def net_worth(
    db: Session, owner: uuid.UUID, currency: str, before: date, account_id: uuid.UUID | None
) -> dict[str, Any]:
    assets, liabilities = ZERO, ZERO
    lines, missing, refs = [], [], []
    for account in balance_accounts(db, owner, before):
        if not account.include_in_net_worth or (account_id and account.id != account_id):
            continue
        native = Decimal(account.balance)
        quote = None
        if account.currency == currency or native == 0:
            converted: Decimal | None = native
        else:
            quote = db.scalar(
                select(FxQuote)
                .where(
                    FxQuote.owner_id == owner,
                    FxQuote.currency == account.currency,
                    FxQuote.book_currency == currency,
                    FxQuote.effective_on < before,
                )
                .order_by(FxQuote.effective_on.desc(), FxQuote.created_at.desc(), FxQuote.id.desc())
                .limit(1)
            )
            converted = convert(db, native, quote.rate, currency) if quote else None
            if quote:
                refs.append(str(quote.id))
            else:
                missing.append(account.name + "：缺少 " + account.currency + " 估值匯率")
        if converted is not None:
            if account.kind == "credit_card":
                liabilities += converted
            else:
                assets += converted
        lines.append(
            {
                **account.model_dump(mode="json"),
                "converted": number(converted) if converted is not None else None,
                "quote_id": str(quote.id) if quote else None,
                "quote_on": str(quote.effective_on) if quote else None,
                "stale": bool(quote and (before - quote.effective_on).days > 7),
            }
        )
    return {
        "value": None if missing else number(assets - liabilities),
        "assets": None if missing else number(assets),
        "liabilities": None if missing else number(liabilities),
        "status": "partial" if missing else "complete",
        "accounts": lines,
        "missing": missing,
        "quote_refs": refs,
    }


def create_report(db: Session, owner: uuid.UUID, params: ReportInput) -> ReportSnapshot:
    # All ledger/settings/valuation writers take this row lock. Snapshot reads cannot interleave
    # with a write, including account/category rename, and frozen exports never requery the ledger.
    book = book_lock(db, owner)
    assert book.book_currency
    if params.account_id:
        from app.core.models import Account

        owned(db, Account, owner, params.account_id)
    if params.category_id:
        owned(db, Category, owner, params.category_id)
    selected = select_transactions(
        owner,
        params.start,
        params.end_exclusive,
        params.account_id,
        params.category_id,
        params.query,
    )
    ids = selected.with_only_columns(Transaction.id).subquery()
    txns = list(
        db.scalars(
            selected.order_by(
                JournalEntry.occurred_on.desc(), Transaction.created_at.desc(), Transaction.id
            )
        )
    )
    outputs = transaction_outputs(db, owner, txns)
    selected_splits = (
        select(
            Transaction.id.label("transaction_id"),
            Category.id.label("category_id"),
            Category.name,
            Category.kind,
            func.to_char(JournalEntry.occurred_on, "YYYY-MM").label("month"),
            TransactionSplit.book_amount_signed.label("value"),
        )
        .select_from(TransactionSplit)
        .join(Category, Category.id == TransactionSplit.category_id)
        .join(JournalEntry, JournalEntry.id == TransactionSplit.entry_id)
        .join(Transaction, Transaction.current_entry_id == JournalEntry.id)
        .where(Transaction.id.in_(select(ids.c.id)), TransactionSplit.owner_id == owner)
    )
    if params.category_id:
        selected_splits = selected_splits.where(
            (Category.id == params.category_id) | (Category.parent_id == params.category_id)
        )
    income, expense = ZERO, ZERO
    categories: dict[str, dict[str, Any]] = {}
    monthly: dict[str, dict[str, Decimal]] = {}
    row_values: dict[uuid.UUID, dict[str, Decimal]] = {}
    for row in db.execute(selected_splits):
        value = -row.value if row.kind == "income" else row.value
        if row.kind == "income":
            income += value
        else:
            expense += value
        cat = categories.setdefault(
            str(row.category_id),
            {"id": str(row.category_id), "name": row.name, "kind": row.kind, "value": ZERO},
        )
        cat["value"] += value
        monthly.setdefault(row.month, {"income": ZERO, "expense": ZERO})[row.kind] += value
        row_values.setdefault(row.transaction_id, {"income": ZERO, "expense": ZERO})[row.kind] += (
            value
        )
    rows = [
        {
            **row.model_dump(mode="json"),
            **{
                key: number(value)
                for key, value in row_values.get(row.id, {"income": ZERO, "expense": ZERO}).items()
            },
        }
        for row in outputs
    ]
    worth = net_worth(db, owner, book.book_currency, params.end_exclusive, params.account_id)
    # Category/search filters describe cash flow, not ownership of account balances.
    if params.category_id or params.query:
        worth["value"], worth["status"] = None, "not_applicable"
    trend = []
    cursor = params.start.replace(day=1)
    while cursor < params.end_exclusive:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        boundary = min(next_month, params.end_exclusive)
        month = cursor.strftime("%Y-%m")
        monthly.setdefault(month, {"income": ZERO, "expense": ZERO})
        if not params.category_id and not params.query:
            point = net_worth(db, owner, book.book_currency, boundary, params.account_id)
            trend.append(
                {
                    "date": str(boundary - timedelta(days=1)),
                    "value": point["value"],
                    "status": "reconstructed" if point["value"] is not None else "partial",
                    "quote_refs": point["quote_refs"],
                }
            )
        cursor = next_month
    snapshot_id = uuid.uuid4()
    doc = {
        "metadata": {
            "snapshot_id": str(snapshot_id),
            "schema_version": 1,
            "rules_version": "cashflow-v1",
            "period_start": str(params.start),
            "period_end_exclusive": str(params.end_exclusive),
            "timezone": book.timezone,
            "report_currency": book.book_currency,
            "filters": params.model_dump(mode="json"),
            "as_of": now().isoformat(),
            "source_revisions": {
                "ledger": book.ledger_revision,
                "valuation": book.valuation_revision,
                "settings": book.settings_revision,
            },
            "quote_refs": sorted(
                set(worth["quote_refs"] + [ref for point in trend for ref in point["quote_refs"]])
            ),
        },
        "metrics": {
            "income": {"value": number(income), "status": "complete"},
            "expense": {"value": number(expense), "status": "complete"},
            "balance": {"value": number(income - expense), "status": "complete"},
            "savings_rate": {
                "value": number(((income - expense) / income * 100).quantize(Decimal("0.01")))
                if income > 0
                else None,
                "status": "complete" if income > 0 else "not_applicable",
            },
            "net_worth": {"value": worth["value"], "status": worth["status"]},
        },
        "categories": [{**cat, "value": number(cat["value"])} for cat in categories.values()],
        "monthly": [
            {
                "month": month,
                **{key: number(value) for key, value in values.items()},
                "balance": number(values["income"] - values["expense"]),
            }
            for month, values in sorted(monthly.items())
        ],
        "net_worth_trend": trend,
        "accounts": worth["accounts"],
        "rows": rows,
        "warnings": worth["missing"]
        + [
            "淨資產歷史曲線依目前有效帳本與已保存匯率重建，非當日已封存快照。",
            "預算與投資尚未啟用。",
        ]
        + (
            ["有估值匯率距離報表日期超過 7 天，請確認是否需要更新。"]
            if any(a["stale"] for a in worth["accounts"])
            else []
        )
        + (
            ["分類或關鍵字篩選不適用淨資產；餘額明細仍為所選帳戶的期末餘額。"]
            if params.category_id or params.query
            else []
        ),
    }
    snapshot = ReportSnapshot(
        id=snapshot_id,
        owner_id=owner,
        document=doc,
        content_hash=hashlib.sha256(
            json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def text_cell(value: Any) -> str:
    value = str(value)
    return (
        "'" + value
        if value.lstrip(" \t\r\n\ufeff").startswith(("=", "+", "-", "@"))
        or value.startswith(("\t", "\r", "\n"))
        else value
    )


def csv_bytes(headers: list[str], rows: list[list[Any]], numeric: set[int] | None = None) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(
            [
                str(value) if numeric and i in numeric else text_cell(value)
                for i, value in enumerate(row)
            ]
        )
    return out.getvalue().encode("utf-8-sig")


def report_zip(snapshot: ReportSnapshot) -> bytes:
    doc = snapshot.document
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "metadata.json",
            json.dumps(
                {
                    **doc["metadata"],
                    "content_hash": snapshot.content_hash,
                    "warnings": doc["warnings"],
                    "csv_text_policy": "formula-like text prefixed with apostrophe",
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "transactions.csv",
            csv_bytes(
                [
                    "交易ID",
                    "日期",
                    "類型",
                    "帳戶",
                    "轉入帳戶",
                    "原幣",
                    "原幣金額",
                    "收入（基準幣）",
                    "支出淨額（基準幣）",
                    "分類",
                    "商家",
                    "備註",
                    "標籤",
                ],
                [
                    [
                        r["id"],
                        r["occurred_on"],
                        r["kind"],
                        r["account"],
                        r["to_account"],
                        r["currency"],
                        r["amount"],
                        r["income"],
                        r["expense"],
                        " / ".join(r["categories"]),
                        r["merchant"],
                        r["note"],
                        " / ".join(r["tags"]),
                    ]
                    for r in doc["rows"]
                ],
                {6, 7, 8},
            ),
        )
        archive.writestr(
            "categories.csv",
            csv_bytes(
                ["分類ID", "分類", "類型", "金額"],
                [[c["id"], c["name"], c["kind"], c["value"]] for c in doc["categories"]],
                {3},
            ),
        )
        archive.writestr(
            "monthly.csv",
            csv_bytes(
                ["月份", "收入", "支出淨額", "結餘"],
                [[m["month"], m["income"], m["expense"], m["balance"]] for m in doc["monthly"]],
                {1, 2, 3},
            ),
        )
        archive.writestr(
            "summary.csv",
            csv_bytes(
                ["指標", "數值", "狀態"],
                [
                    [name, m["value"] if m["value"] is not None else "", m["status"]]
                    for name, m in doc["metrics"].items()
                ],
                {1},
            ),
        )
        archive.writestr(
            "accounts.csv",
            csv_bytes(
                ["帳戶", "類型", "幣別", "餘額或欠款", "換算基準幣", "估值匯率日期"],
                [
                    [
                        a["name"],
                        a["kind"],
                        a["currency"],
                        a["balance"],
                        a["converted"] if a["converted"] is not None else "",
                        a["quote_on"] or "",
                    ]
                    for a in doc["accounts"]
                ],
                {3, 4},
            ),
        )
    return output.getvalue()
