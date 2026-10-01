"""Persistent local-calendar cursor; all financial effects use the common ledger."""

import calendar
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.db import SCHEDULER_LOCK, transaction
from app.core.jobs import enqueue
from app.core.models import Account, Job, RecurringOccurrence, RecurringRule, Transaction, User, now
from app.ledger import service as ledger
from app.ledger.schemas import TransactionInput
from app.recurring.schemas import RuleInput

BATCH_SIZE = 100


def scheduled_at(day: date, clock: str, timezone: str) -> datetime:
    """Choose fold=0; gaps move to the first valid wall-clock second (not +1 hour)."""
    zone = ZoneInfo(timezone)
    wall = datetime.combine(day, time.fromisoformat(clock))
    # Also covers historical whole-day jumps, e.g. Pacific/Apia.
    for _ in range(172801):
        candidate = wall.replace(tzinfo=zone, fold=0).astimezone(UTC)
        if candidate.astimezone(zone).replace(tzinfo=None) == wall:
            return candidate
        wall += timedelta(seconds=1)
    raise ValueError("unresolvable_local_time")


def next_date(rule: RecurringRule, day: date) -> date | None:
    try:
        if rule.frequency == "monthly":
            month = day.year * 12 + day.month - 1 + rule.interval
            year, index = divmod(month, 12)
            result = date(
                year, index + 1, min(rule.anchor_date.day, calendar.monthrange(year, index + 1)[1])
            )
        else:
            result = day + timedelta(days=rule.interval * (7 if rule.frequency == "weekly" else 1))
    except (ValueError, OverflowError):
        return None
    return None if rule.end_on and result > rule.end_on else result


def set_cursor(rule: RecurringRule, day: date | None) -> None:
    rule.next_local_date = day
    rule.next_due_at = scheduled_at(day, rule.local_time, rule.timezone) if day else None


def first_future_date(rule: RecurringRule, instant: datetime) -> date | None:
    """Jump close to today arithmetically instead of walking decades under the book lock."""
    today = instant.astimezone(ZoneInfo(rule.timezone)).date()
    day: date | None = rule.anchor_date
    if today > rule.anchor_date:
        if rule.frequency == "monthly":
            elapsed = (
                (today.year - rule.anchor_date.year) * 12 + today.month - rule.anchor_date.month
            )
            month = (
                rule.anchor_date.year * 12
                + rule.anchor_date.month
                - 1
                + (elapsed // rule.interval) * rule.interval
            )
            year, index = divmod(month, 12)
            day = date(
                year, index + 1, min(rule.anchor_date.day, calendar.monthrange(year, index + 1)[1])
            )
        else:
            step = rule.interval * (7 if rule.frequency == "weekly" else 1)
            day = rule.anchor_date + timedelta(
                days=((today - rule.anchor_date).days // step) * step
            )
    while day and scheduled_at(day, rule.local_time, rule.timezone) <= instant:
        day = next_date(rule, day)
    return None if day and rule.end_on and day > rule.end_on else day


def save_rule(
    db: Session,
    owner: uuid.UUID,
    data: RuleInput,
    identity: uuid.UUID | None = None,
    expected_revision: int | None = None,
) -> RecurringRule:
    book = ledger.book_lock(db, owner)
    assert book.book_currency
    account = ledger.active_account(db, owner, data.template.account_id)
    template = data.template
    amount = ledger.money(db, template.amount, account.currency)
    ledger.rate(account.currency, book.book_currency, template.fx_rate)
    if template.kind == "transfer":
        if (
            not template.to_account_id
            or template.to_account_id == account.id
            or template.category_id
            or template.splits
        ):
            ledger.fail("轉帳需選擇不同帳戶，且本金不使用收支分類。")
        target = ledger.active_account(db, owner, template.to_account_id)
        incoming = ledger.money(db, template.received_amount or template.amount, target.currency)
        if incoming <= 0 or (target.currency == account.currency and incoming != amount):
            ledger.fail("轉入金額不正確。")
        if target.currency != account.currency and template.received_amount is None:
            ledger.fail("跨幣轉帳需指定轉入金額。")
        ledger.rate(
            target.currency,
            book.book_currency,
            template.received_fx_rate if target.currency != account.currency else template.fx_rate,
        )
        fee = ledger.money(db, template.fee, account.currency)
        if fee:
            if not template.fee_category_id:
                ledger.fail("請選擇手續費分類。")
            ledger.validate_category(db, owner, template.fee_category_id, "expense")
    elif template.kind == "refund":
        if not template.refund_of_id or template.category_id or template.splits:
            ledger.fail("退款須關聯原支出並沿用分類。")
        original = ledger.owned(db, Transaction, owner, template.refund_of_id)
        ledger.refund_splits(db, owner, original, amount, account.currency, uuid.UUID(int=0))
    else:
        if bool(template.category_id) == bool(template.splits):
            ledger.fail("請選擇分類或提供完整分攤。")
        if template.category_id:
            ledger.validate_category(db, owner, template.category_id, template.kind)
        if template.splits:
            amounts = [
                ledger.money(db, split.amount, account.currency) for split in template.splits
            ]
            if any(value <= 0 for value in amounts) or sum(amounts) != amount:
                ledger.fail("分攤合計必須等於交易金額且為正。")
            for split in template.splits:
                ledger.validate_category(db, owner, split.category_id, template.kind)
    if identity:
        rule = db.scalar(
            select(RecurringRule)
            .where(RecurringRule.id == identity, RecurringRule.owner_id == owner)
            .with_for_update()
        )
        if not rule:
            ledger.fail("找不到週期規則。", "not_found", 404)
        if rule.revision != expected_revision:
            ledger.fail("規則已變更，請重新載入。", "revision_conflict", 409)
        # Drain catch-up before editing so elapsed dates cannot silently change revision.
        if data.enabled and rule.enabled and rule.next_due_at and rule.next_due_at <= now():
            ledger.fail("請先完成到期補跑再修改規則。", "catchup_pending", 409)
        rule.revision += 1
    else:
        rule = RecurringRule(owner_id=owner, revision=1)
        db.add(rule)
    for name in (
        "frequency",
        "interval",
        "anchor_date",
        "timezone",
        "end_on",
        "posting_mode",
        "enabled",
    ):
        setattr(rule, name, getattr(data, name))
    rule.local_time = data.local_time.isoformat()
    rule.template = {"schema_version": 1, "transaction": data.template.model_dump(mode="json")}
    if not identity:
        set_cursor(rule, rule.anchor_date)
    else:
        # Edits apply after their wall-clock instant; existing occurrences retain snapshots.
        set_cursor(rule, first_future_date(rule, now()))
    if identity:
        # Persist cancellation now: re-enabling before a queued job runs must not revive it.
        pending = update(RecurringOccurrence).where(
            RecurringOccurrence.rule_id == rule.id,
            RecurringOccurrence.status == "expected",
            RecurringOccurrence.posting_mode == "auto_post",
        )
        if not rule.enabled:
            db.execute(pending.values(status="skipped"))
        elif rule.end_on:
            db.execute(
                pending.where(RecurringOccurrence.scheduled_local_date > rule.end_on).values(
                    status="skipped"
                )
            )
    db.flush()
    return rule


def scan_due(at: datetime | None = None, limit: int = BATCH_SIZE) -> int:
    instant = at or now()
    if instant.tzinfo is None or not 1 <= limit <= 1000:
        raise ValueError("aware time and limit 1..1000 required")
    count = 0
    with transaction() as db:
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": SCHEDULER_LOCK}):
            return 0
        owners = list(
            db.scalars(
                select(RecurringRule.owner_id)
                .join(User, User.id == RecurringRule.owner_id)
                .where(
                    RecurringRule.enabled,
                    RecurringRule.next_due_at <= instant,
                    User.disabled_at.is_(None),
                )
                .distinct()
            )
        )
        for owner in owners:
            ledger.book_lock(db, owner)
            rules = db.scalars(
                select(RecurringRule)
                .where(
                    RecurringRule.owner_id == owner,
                    RecurringRule.enabled,
                    RecurringRule.next_due_at <= instant,
                )
                .order_by(RecurringRule.next_due_at, RecurringRule.id)
                .with_for_update()
            )
            for rule in rules:
                while rule.next_due_at and rule.next_due_at <= instant and count < limit:
                    day = rule.next_local_date
                    assert day
                    if rule.end_on and day > rule.end_on:
                        set_cursor(rule, None)
                        break
                    existing = db.scalar(
                        select(RecurringOccurrence).where(
                            RecurringOccurrence.rule_id == rule.id,
                            RecurringOccurrence.scheduled_local_date == day,
                        )
                    )
                    if not existing:
                        account = ledger.owned(
                            db,
                            Account,
                            owner,
                            uuid.UUID(rule.template["transaction"]["account_id"]),
                        )
                        occurrence = RecurringOccurrence(
                            owner_id=owner,
                            rule_id=rule.id,
                            scheduled_local_date=day,
                            scheduled_at=rule.next_due_at,
                            expected_amount=Decimal(rule.template["transaction"]["amount"]),
                            currency=account.currency,
                            rule_revision=rule.revision,
                            template=rule.template,
                            posting_mode=rule.posting_mode,
                        )
                        db.add(occurrence)
                        db.flush()
                        if rule.posting_mode == "auto_post":
                            enqueue(
                                db,
                                owner,
                                "recurring_post",
                                f"occurrence:{occurrence.id}",
                                {"occurrence_id": str(occurrence.id)},
                            )
                    set_cursor(rule, next_date(rule, day))
                    count += 1
                if count >= limit:
                    return count
    return count


def post_occurrence(job_id: uuid.UUID, token: uuid.UUID) -> None:
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if (
            not job
            or job.kind != "recurring_post"
            or job.status != "running"
            or job.lease_token != token
            or not job.lease_until
            or job.lease_until <= now()
        ):
            raise RuntimeError("lease_lost")
        ledger.book_lock(db, job.owner_id)
        if job.lease_until <= now():
            raise RuntimeError("lease_lost")
        occurrence = ledger.owned(
            db, RecurringOccurrence, job.owner_id, uuid.UUID(job.payload["occurrence_id"])
        )
        rule = ledger.owned(db, RecurringRule, job.owner_id, occurrence.rule_id)
        if occurrence.status != "expected" or occurrence.posting_mode != "auto_post":
            return
        user = db.get(User, job.owner_id)
        if (
            not rule.enabled
            or not user
            or user.disabled_at
            or (rule.end_on and occurrence.scheduled_local_date > rule.end_on)
        ):
            occurrence.status = "skipped"
            return
        data = TransactionInput.model_validate(occurrence.template["transaction"]).model_copy(
            update={"occurred_on": occurrence.scheduled_local_date}
        )
        occurrence.transaction_id = ledger.create_transaction(
            db, job.owner_id, data, f"recurring:{occurrence.id}"
        )
        occurrence.status = "posted"
        if job.lease_until <= now():
            raise RuntimeError("lease_lost")
        # Posting is not evidence of a bank charge: actual_verified_at stays NULL.
