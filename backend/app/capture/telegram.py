import shlex
import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.jobs import enqueue
from app.core.models import Account, CaptureDraft, Category, TelegramCursor, TelegramEvent
from app.ledger.service import book_lock, fail, owned

from . import service
from .schemas import DraftAction, DraftEdit, Proposal, TelegramUpdate


def send(
    db: Session,
    owner: uuid.UUID,
    chat: int,
    key: str,
    text: str,
    buttons: list[list[dict[str, str]]] | None = None,
) -> None:
    payload: dict[str, Any] = {"chat_id": chat, "text": text[:4000]}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    enqueue(db, owner, "telegram_send", key, payload)


def button(label: str, action: str, row: CaptureDraft, extra: str = "") -> dict[str, str]:
    value = f"{action}:{row.id.hex}:{row.revision}" + (f":{extra}" if extra else "")
    assert len(value.encode()) <= 64
    return {"text": label[:60], "callback_data": value}


def show_draft(db: Session, row: CaptureDraft, key: str) -> None:
    if not row.chat_id:
        return
    p = Proposal.model_validate(row.proposal)
    account = db.get(Account, p.account_id) if p.account_id else None
    category = db.get(Category, p.category_id) if p.category_id else None
    labels = {
        "confirmed": "已入帳",
        "cancelled": "已取消",
        "processing": "處理中",
        "failed": "處理失敗，可手動填寫",
        "needs_review": "待確認",
    }
    text = f"草稿 {row.id.hex[:8]} · {labels[row.status]}\n{p.merchant or '商家待補'}\n日期：{p.occurred_on or '待補'}\n金額：{p.currency or '幣別待補'} {p.amount or '待補'}\n帳戶：{account.name if account else '待選'}\n分類：{category.name if category else '已分攤' if p.splits else '待選'}"
    if row.warnings:
        text += "\n提醒：" + "；".join(row.warnings)
    buttons = None
    if row.status not in {"confirmed", "cancelled"}:
        text += f'\n修改：/edit {row.id.hex[:8]} amount=金額 date=YYYY-MM-DD currency=USD merchant="商家"\n也可在 Mac 網頁的「收據草稿」完整編輯。確認即表示已核對上述提醒。'
        buttons = [
            [button("選擇帳戶", "accounts", row), button("選擇分類", "categories", row)],
            [button("確認入帳", "confirm", row), button("取消草稿", "cancel", row)],
        ]
    send(db, row.owner_id, row.chat_id, key, text, buttons)


def choices(db: Session, row: CaptureDraft, action: str, page: int, key: str) -> None:
    if not row.chat_id:
        return
    cls: Any = Account if action == "accounts" else Category
    query = (
        select(cls)
        .where(cls.owner_id == row.owner_id, cls.archived_at.is_(None))
        .order_by(cls.name, cls.id)
    )
    if cls is Category:
        query = query.where(Category.kind == row.proposal.get("kind", "expense"))
    all_rows = list(db.scalars(query))
    page = max(0, min(page, max(0, (len(all_rows) - 1) // 8)))
    buttons = [
        [
            button(
                f"{r.name}" + (f" · {r.currency}" if isinstance(r, Account) else ""),
                "acct" if cls is Account else "cat",
                row,
                r.id.hex[:8],
            )
        ]
        for r in all_rows[page * 8 : page * 8 + 8]
    ]
    nav = []
    if page:
        nav.append(button("上一頁", action, row, str(page - 1)))
    if len(all_rows) > (page + 1) * 8:
        nav.append(button("下一頁", action, row, str(page + 1)))
    if nav:
        buttons.append(nav)
    send(
        db,
        row.owner_id,
        row.chat_id,
        key,
        "請選擇帳戶" if cls is Account else "請選擇分類",
        buttons or None,
    )


def callback(db: Session, owner: uuid.UUID, value: str, key: str, request: Any, chat: int) -> None:
    parts = value.split(":")
    if len(parts) not in {3, 4}:
        fail("按鈕無效，請輸入 /pending。", "callback_invalid")
    action, ident, revision = parts[:3]
    row = owned(db, CaptureDraft, owner, uuid.UUID(ident))
    row.chat_id = chat
    rev = int(revision)
    if action == "view":
        show_draft(db, row, key)
        return
    if action == "confirm":
        service.confirm(
            db,
            owner,
            row.id,
            DraftAction(expected_revision=rev, acknowledge_warnings=True),
            request,
        )
    elif action == "cancel":
        service.cancel(db, owner, row.id, rev, request)
    else:
        service.editable(row, rev)
        if action in {"accounts", "categories"}:
            choices(db, row, action, int(parts[3]) if len(parts) > 3 else 0, key)
            return
        if action not in {"acct", "cat"} or len(parts) != 4:
            fail("按鈕無效，請輸入 /pending。", "callback_invalid")
        cls: Any = Account if action == "acct" else Category
        matches = [
            r
            for r in db.scalars(select(cls).where(cls.owner_id == owner, cls.archived_at.is_(None)))
            if r.id.hex.startswith(parts[3])
        ]
        if len(matches) != 1:
            fail("選項已變更，請重新選擇。", "selection_changed", 409)
        p = Proposal.model_validate(row.proposal)
        if action == "acct":
            p.account_id = matches[0].id
            # Never silently replace an OCR currency with the selected account currency.
        else:
            p.category_id = matches[0].id
            p.splits = []
        service.save_draft(db, owner, row.id, DraftEdit(expected_revision=rev, proposal=p))
    show_draft(db, row, key)


def command(db: Session, owner: uuid.UUID, update: TelegramUpdate, key: str, request: Any) -> None:
    assert update.chat_id
    chat = update.chat_id
    name = update.text.split()[0].split("@")[0].lower()
    if name == "/pending":
        parts = update.text.split()
        page = int(parts[1]) if len(parts) > 1 else 1
        if not 1 <= page <= 10000:
            raise ValueError("page")
        rows = list(
            db.scalars(
                select(CaptureDraft)
                .where(
                    CaptureDraft.owner_id == owner,
                    CaptureDraft.status.not_in(["confirmed", "cancelled"]),
                )
                .order_by(CaptureDraft.created_at.desc())
                .offset((page - 1) * 10)
                .limit(11)
            )
        )
        buttons = [
            [button(f"{r.id.hex[:8]} · {r.proposal.get('merchant') or '待確認'}", "view", r)]
            for r in rows[:10]
        ]
        send(
            db,
            owner,
            chat,
            key,
            f"待確認草稿 · 第 {page} 頁"
            + (f"\n下一頁：/pending {page + 1}" if len(rows) > 10 else "")
            if rows
            else "目前沒有待確認草稿。",
            buttons or None,
        )
    elif name == "/edit":
        parts = shlex.split(update.text)
        if len(parts) < 3 or len(parts[1]) < 8:
            raise ValueError("edit")
        rows = [
            r
            for r in db.scalars(
                select(CaptureDraft).where(
                    CaptureDraft.owner_id == owner,
                    CaptureDraft.status.not_in(["confirmed", "cancelled"]),
                )
            )
            if r.id.hex.startswith(parts[1])
        ]
        if len(rows) != 1:
            fail("找不到唯一草稿，請輸入 /pending。", "draft_not_found")
        row = rows[0]
        proposal = dict(row.proposal)
        mapping = {
            "amount": "amount",
            "date": "occurred_on",
            "currency": "currency",
            "merchant": "merchant",
            "note": "note",
        }
        for part in parts[2:]:
            field, val = part.split("=", 1)
            if field not in mapping:
                raise ValueError("field")
            proposal[mapping[field]] = val
        service.save_draft(
            db,
            owner,
            row.id,
            DraftEdit(expected_revision=row.revision, proposal=Proposal.model_validate(proposal)),
        )
        row.chat_id = chat
        show_draft(db, row, key)
    elif name in {"/today", "/month", "/networth"}:
        from app.ledger.reports import create_report
        from app.ledger.schemas import ReportInput

        book = book_lock(db, owner)
        today = datetime.now(ZoneInfo(book.timezone or "UTC")).date()
        start = today if name == "/today" else today.replace(day=1)
        report = create_report(
            db, owner, ReportInput(start=start, end_exclusive=today + timedelta(days=1))
        )
        metrics = report.document["metrics"]
        fields = (
            [("淨資產", "net_worth")]
            if name == "/networth"
            else [("收入", "income"), ("支出淨額", "expense"), ("收支結餘", "balance")]
        )
        lines = [f"{start} 至 {today} · {book.book_currency}"]
        for label, field in fields:
            metric = metrics.get(field, {})
            lines.append(
                f"{label}：{metric.get('value') if metric.get('value') is not None else '資料不足'}"
            )
        send(db, owner, chat, key, "\n".join(lines))
    elif name == "/budget":
        send(db, owner, chat, key, "預算功能尚未實作（Phase 3）。")
    else:
        send(
            db,
            owner,
            chat,
            key,
            '傳送收據照片或記帳文字，即可建立草稿。\n/pending 待確認草稿\n/today 今日收支\n/month 本月收支\n/networth 淨資產\n/edit 草稿編號 amount=金額 date=YYYY-MM-DD currency=USD merchant="商家"\n照片辨識不會自動入帳，請核對後按確認。',
        )


def ingest(
    db: Session, owner: uuid.UUID, update: TelegramUpdate, request: Any, failure: str | None = None
) -> dict[str, Any]:
    cfg = settings()
    if not cfg.telegram_bot_id or update.bot_id != cfg.telegram_bot_id:
        fail("Telegram 尚未設定或 Bot 不符。", "telegram_disabled", 403)
    book_lock(db, owner)
    cursor = db.get(TelegramCursor, update.bot_id)
    if not cursor:
        cursor = TelegramCursor(bot_id=update.bot_id, next_offset=0)
        db.add(cursor)
        db.flush()
    prior = db.scalar(
        select(TelegramEvent).where(
            TelegramEvent.bot_id == update.bot_id, TelegramEvent.update_id == update.update_id
        )
    )
    if prior:
        return {"next_offset": cursor.next_offset, "accepted": prior.accepted}
    accepted = bool(
        update.private
        and cfg.telegram_user_id
        and update.user_id == cfg.telegram_user_id
        and update.chat_id == cfg.telegram_user_id
    )
    db.add(
        TelegramEvent(
            owner_id=owner, bot_id=update.bot_id, update_id=update.update_id, accepted=accepted
        )
    )
    key = f"tg:{update.bot_id}:{update.update_id}"
    if accepted:
        assert update.chat_id
        if failure:
            send(db, owner, update.chat_id, key + ":error", failure)
        else:
            if update.callback:
                callback(db, owner, update.callback, key, request, update.chat_id)
            elif update.file_id:
                row = service.new_draft(
                    db,
                    owner,
                    key,
                    source="telegram",
                    text=update.text[:2000],
                    chat=update.chat_id,
                    download={"file_id": update.file_id, "filename": update.filename},
                )
                send(
                    db,
                    owner,
                    update.chat_id,
                    key,
                    f"已收到收據，正在下載。草稿 {row.id.hex[:8]}，完成後會通知你。",
                )
            elif update.text.startswith("/"):
                command(db, owner, update, key, request)
            elif update.text.strip():
                row = service.new_draft(
                    db,
                    owner,
                    key,
                    text=update.text[:2000],
                    source="telegram",
                    chat=update.chat_id,
                )
                show_draft(db, row, key)
            else:
                send(db, owner, update.chat_id, key, "請傳送 JPEG、PNG、HEIC 收據圖片或文字。")
    # Telegram chooses a random update_id after a week of silence. Do not discard unseen IDs
    # merely because they are lower than the previous offset; persist event before acknowledging.
    cursor.next_offset = update.update_id + 1
    db.flush()
    return {"next_offset": cursor.next_offset, "accepted": accepted}
