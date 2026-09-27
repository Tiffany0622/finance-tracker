"""Read-only, explainable category candidates; never classify or post a draft."""

import re
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import (
    CaptureDraft,
    Category,
    JournalEntry,
    ReceiptItem,
    ReceiptItemReview,
    Transaction,
    TransactionSplit,
)
from app.core.schemas import StrictModel
from app.ledger.service import owned

from .service import editable


class SuggestionInput(StrictModel):
    expected_revision: int = Field(ge=1)
    kind: Literal["expense", "income"]
    merchant: str = Field(max_length=200)
    note: str = Field(max_length=2000)


class CategorySuggestion(BaseModel):
    category_id: uuid.UUID | None
    name: str
    source: Literal["merchant_history", "receipt_text", "common", "fallback"]
    reason: str


class CategorySuggestions(BaseModel):
    draft_revision: int
    suggestions: list[CategorySuggestion]
    message: str


@dataclass(frozen=True)
class Rule:
    kind: str
    name: str
    aliases: tuple[str, ...]
    keywords: tuple[str, ...]


# A small, auditable vocabulary rather than opaque confidence percentages or
# merchant-brand assumptions. Existing category names remain the user's choice.
RULES = (
    Rule(
        "expense",
        "會員／訂閱",
        ("會員費", "會員", "訂閱", "subscriptions"),
        ("membership", "subscription", "會員", "訂閱"),
    ),
    Rule(
        "expense",
        "食材／雜貨",
        ("食物", "飲食", "餐飲", "食材", "買菜", "雜貨", "groceries"),
        (
            "grocery",
            "groceries",
            "supermarket",
            "milk",
            "bread",
            "apples",
            "eggs",
            "牛奶",
            "蔬菜",
            "食材",
            "超市",
            "買菜",
            "麵包",
            "鮮乳",
        ),
    ),
    Rule(
        "expense",
        "外食／咖啡",
        ("外食", "飲食", "餐飲", "咖啡", "restaurants", "dining"),
        ("restaurant", "cafe", "coffee", "latte", "餐廳", "外食", "午餐", "晚餐", "咖啡"),
    ),
    Rule(
        "expense",
        "交通",
        ("交通費", "通勤", "transportation"),
        (
            "parking",
            "uber",
            "lyft",
            "gasoline",
            "fuel",
            "停車",
            "加油",
            "捷運",
            "高鐵",
            "公車",
            "計程車",
        ),
    ),
    Rule(
        "expense",
        "居家／日用品",
        ("居家", "日用品", "生活用品", "household"),
        ("detergent", "toilet paper", "cleaner", "日用品", "洗衣精", "衛生紙", "清潔用品"),
    ),
    Rule(
        "expense",
        "服飾",
        ("衣服", "衣飾", "clothing"),
        ("shirt", "jeans", "dress", "shoes", "衣服", "服飾", "鞋子", "褲子"),
    ),
    Rule(
        "expense",
        "休閒娛樂",
        ("娛樂", "休閒", "entertainment"),
        ("admission", "cinema", "movie", "門票", "電影", "娛樂"),
    ),
    Rule(
        "expense",
        "醫療保健",
        ("醫療", "健康", "保健", "healthcare"),
        ("pharmacy", "hospital", "dental", "medicine", "醫院", "診所", "藥局", "醫療"),
    ),
    Rule("expense", "房租", ("租金", "住房", "rent"), ("rent", "房租", "租金")),
    Rule(
        "expense",
        "水電／通訊",
        ("水電", "通訊", "utilities"),
        ("electricity", "internet bill", "phone bill", "水費", "電費", "瓦斯費", "電信費"),
    ),
    Rule(
        "income",
        "薪資",
        ("薪水", "工資", "salary", "payroll"),
        ("salary", "payroll", "wages", "薪資", "薪水", "工資", "獎金"),
    ),
    Rule("income", "利息收入", ("利息", "interest"), ("interest", "利息")),
    Rule(
        "income",
        "股息收入",
        ("股息", "股利", "dividends"),
        ("dividend", "dividends", "股息", "股利"),
    ),
)


def normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def category_key(value: str) -> str:
    return "".join(c for c in normalized(value) if c.isalnum())


def contains(text: str, keyword: str) -> bool:
    if keyword.isascii():
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])", text))
    return keyword in text


def candidates(
    db: Session, owner: uuid.UUID, identity: uuid.UUID, body: SuggestionInput
) -> CategorySuggestions:
    row = owned(db, CaptureDraft, owner, identity)
    editable(row, body.expected_revision)
    categories = list(db.scalars(select(Category).where(Category.owner_id == owner)))
    all_categories = {c.id: c for c in categories}
    available = {c.id: c for c in categories if c.kind == body.kind and c.archived_at is None}

    def label(category: Category) -> str:
        parent = all_categories.get(category.parent_id) if category.parent_id else None
        return f"{parent.name} → {category.name}" if parent else category.name

    # Bound preview work. Suggestions describe this recent sample, not all history.
    recent = list(
        db.scalars(
            select(Transaction)
            .join(JournalEntry, JournalEntry.id == Transaction.current_entry_id)
            .where(
                Transaction.owner_id == owner,
                Transaction.status == "posted",
                Transaction.kind == body.kind,
            )
            .order_by(
                JournalEntry.occurred_on.desc(), Transaction.created_at.desc(), Transaction.id
            )
            .limit(500)
        )
    )
    split_ids: dict[uuid.UUID, set[uuid.UUID]] = {}
    entry_ids = [t.current_entry_id for t in recent]
    if entry_ids:
        for entry, category in db.execute(
            select(TransactionSplit.entry_id, TransactionSplit.category_id).where(
                TransactionSplit.owner_id == owner,
                TransactionSplit.entry_id.in_(entry_ids),
                TransactionSplit.category_id.in_(available),
            )
        ):
            split_ids.setdefault(entry, set()).add(category)
    merchant_counts: Counter[uuid.UUID] = Counter()
    common_counts: Counter[uuid.UUID] = Counter()
    merchant = normalized(body.merchant)
    for txn in recent:
        if txn.current_entry_id is None:
            continue
        for category in sorted(split_ids.get(txn.current_entry_id, set()), key=str):
            common_counts[category] += 1
            if merchant and normalized(txn.merchant) == merchant:
                merchant_counts[category] += 1

    suggestions: list[CategorySuggestion] = []
    seen: set[str] = set()

    def add(
        category: Category | None,
        name: str,
        source: Literal["merchant_history", "receipt_text", "common", "fallback"],
        reason: str,
    ) -> None:
        key = str(category.id) if category else category_key(name)
        if key in seen or len(suggestions) >= 3:
            return
        seen.add(key)
        suggestions.append(
            CategorySuggestion(
                category_id=category.id if category else None,
                name=label(category) if category else name,
                source=source,
                reason=reason,
            )
        )

    for identity_category, count in merchant_counts.most_common(3):
        add(
            available[identity_category],
            "",
            "merchant_history",
            f"近 500 筆同類型交易中，這家商店有 {count} 筆使用此分類。",
        )

    # Current, human-reviewed item names supersede the OCR names, including an
    # explicitly empty review. A stale review must not override a newer draft.
    review = db.scalar(
        select(ReceiptItemReview)
        .where(ReceiptItemReview.owner_id == owner, ReceiptItemReview.draft_id == row.id)
        .order_by(ReceiptItemReview.revision.desc())
        .limit(1)
    )
    if review and review.draft_revision == row.revision and review.transaction_revision is None:
        item_names = list(
            db.scalars(
                select(ReceiptItem.name).where(
                    ReceiptItem.owner_id == owner, ReceiptItem.review_id == review.id
                )
            )
        )
    else:
        item_names = [i.get("raw_name", "") for i in (row.parsed or {}).get("items", [])]
    text = normalized("\n".join([body.merchant, body.note, *item_names]))
    keyword_found = False
    for rule in RULES:
        if rule.kind != body.kind:
            continue
        matched = next((term for term in rule.keywords if contains(text, term)), None)
        if not matched:
            continue
        keyword_found = True
        names = [category_key(n) for n in (rule.name, *rule.aliases)]
        matches = sorted(
            (c for c in available.values() if category_key(c.name) in names),
            key=lambda c: (names.index(category_key(c.name)), label(c), str(c.id)),
        )
        if matches:
            for match in matches:
                add(
                    match,
                    "",
                    "receipt_text",
                    f"商家、備註或品項出現「{matched}」，可能適合此分類，請核對用途。",
                )
        else:
            # Don't suggest recreating an explicitly archived equivalent.
            archived = any(
                c.kind == body.kind and c.archived_at and category_key(c.name) in names
                for c in categories
            )
            if not archived:
                add(
                    None,
                    rule.name,
                    "receipt_text",
                    f"商家、備註或品項出現「{matched}」；尚無對應分類，可建立後選用。",
                )

    if not suggestions:
        for identity_category, count in common_counts.most_common(3):
            add(
                available[identity_category],
                "",
                "common",
                f"近 500 筆同類型交易有 {count} 筆使用；只是常用選項，尚無證據適合這張收據。",
            )
        if not suggestions:
            for category in sorted(available.values(), key=lambda c: (label(c), str(c.id)))[:3]:
                add(
                    category,
                    "",
                    "fallback",
                    "現有分類供你選擇，目前資訊不足，尚未判定適合這張收據。",
                )
        if not suggestions:
            add(
                None,
                "其他支出" if body.kind == "expense" else "其他收入",
                "fallback",
                "目前資訊不足。可先補充商家、用途或品項，也可自行修改分類名稱後建立。",
            )

    message = "依本機歷史與文字提供候選，請核對後選用；不會自動入帳或推算分攤金額。"
    if not merchant_counts and not keyword_found:
        message = "資訊不足，以下僅供選擇；補充商家、備註或品項後可更新建議。"
    return CategorySuggestions(
        draft_revision=row.revision, suggestions=suggestions, message=message
    )
