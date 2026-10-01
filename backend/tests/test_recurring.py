"""Synthetic PostgreSQL tests with explicit UTC cutoffs and independent calendar outcomes."""

import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_ledger import setup

from app.core.db import engine, transaction
from app.core.jobs import claim, finish
from app.core.models import Job, RecurringRule, Transaction
from app.recurring import service
from app.worker import run_once


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch):
    current = [utc("2024-01-01T00:00:00")]
    monkeypatch.setattr(service, "now", lambda: current[0])
    return current


def body(client, **overrides):
    bank, _, _, food, _, _ = setup(client)
    return (
        dict(
            template=dict(
                kind="expense",
                occurred_on="2024-01-01",
                account_id=bank["id"],
                category_id=food["id"],
                amount="10",
            ),
            frequency="daily",
            interval=1,
            anchor_date="2024-01-01",
            local_time="09:00:00",
            timezone="UTC",
            posting_mode="auto_post",
        )
        | overrides
    )


def create(client, payload, key=None):
    response = client.post(
        "/api/v1/recurring-rules",
        json=payload,
        headers={"Idempotency-Key": key or str(uuid.uuid4())},
    )
    assert response.status_code == 200, response.text
    return response.json()


def occurrences(client, rule):
    response = client.get(f"/api/v1/recurring-rules/{rule['id']}/occurrences")
    assert response.status_code == 200, response.text
    return sorted(response.json(), key=lambda row: row["scheduled_local_date"])


@pytest.mark.parametrize(
    "year,anchor,expected",
    [
        (2023, 28, ["2023-01-28", "2023-02-28", "2023-03-28", "2023-04-28"]),
        (2023, 29, ["2023-01-29", "2023-02-28", "2023-03-29", "2023-04-29"]),
        (2024, 29, ["2024-01-29", "2024-02-29", "2024-03-29", "2024-04-29"]),
        (2024, 30, ["2024-01-30", "2024-02-29", "2024-03-30", "2024-04-30"]),
        (2024, 31, ["2024-01-31", "2024-02-29", "2024-03-31", "2024-04-30"]),
        (2023, 31, ["2023-01-31", "2023-02-28", "2023-03-31", "2023-04-30"]),
    ],
)
def test_month_anchor_never_drifts(logged_in, clock, year, anchor, expected):
    payload = body(
        logged_in,
        frequency="monthly",
        anchor_date=f"{year}-01-{anchor}",
        posting_mode="expect_only",
    )
    rule = create(logged_in, payload)
    assert service.scan_due(utc(f"{year}-05-01T00:00:00")) == 4
    rows = occurrences(logged_in, rule)
    assert [row["scheduled_local_date"] for row in rows] == expected
    assert all(row["status"] == "expected" and row["transaction_id"] is None for row in rows)
    with Session(engine()) as db:
        assert (
            db.scalar(select(func.count()).select_from(Job).where(Job.kind == "recurring_post"))
            == 0
        )


@pytest.mark.parametrize(
    "day,clock,zone,expected",
    [
        ("2024-03-10", "02:30:00", "America/Los_Angeles", "2024-03-10T10:00:00"),
        ("2024-11-03", "01:30:00", "America/Los_Angeles", "2024-11-03T08:30:00"),
        ("2024-03-10", "02:30:00", "America/New_York", "2024-03-10T07:00:00"),
        ("2024-11-03", "01:30:00", "America/New_York", "2024-11-03T05:30:00"),
        ("2024-03-10", "09:00:00", "Asia/Taipei", "2024-03-10T01:00:00"),
        ("2024-03-10", "09:00:00", "Pacific/Honolulu", "2024-03-10T19:00:00"),
    ],
)
def test_dst_and_timezone_exact_cutoff(logged_in, day, clock, zone, expected):
    rule = create(
        logged_in,
        body(
            logged_in,
            anchor_date=day,
            local_time=clock,
            timezone=zone,
            end_on=day,
            posting_mode="expect_only",
        ),
    )
    instant = utc(expected)
    assert service.scan_due(instant - timedelta(seconds=1)) == 0
    assert service.scan_due(instant) == 1
    assert service.scan_due(instant + timedelta(hours=2)) == 0
    rows = occurrences(logged_in, rule)
    assert len(rows) == 1
    assert datetime.fromisoformat(rows[0]["scheduled_at"]) == instant


@pytest.mark.parametrize(
    "frequency,interval,expected",
    [
        ("daily", 2, ["2024-01-01", "2024-01-03", "2024-01-05", "2024-01-07"]),
        ("weekly", 1, ["2024-01-01", "2024-01-08"]),
        ("monthly", 2, ["2024-01-01"]),
    ],
)
def test_interval_and_inclusive_end(logged_in, frequency, interval, expected):
    rule = create(
        logged_in,
        body(
            logged_in,
            frequency=frequency,
            interval=interval,
            end_on="2024-01-08",
            posting_mode="expect_only",
        ),
    )
    assert service.scan_due(utc("2024-02-01T00:00:00")) == len(expected)
    assert [r["scheduled_local_date"] for r in occurrences(logged_in, rule)] == expected
    with Session(engine()) as db:
        row = db.get(RecurringRule, uuid.UUID(rule["id"]))
        assert row.next_due_at is None


def test_disabled_and_owner_disabled(logged_in):
    from app.core.models import User

    rule = create(logged_in, body(logged_in, enabled=False))
    assert service.scan_due(utc("2024-01-05T00:00:00")) == 0
    assert occurrences(logged_in, rule) == []
    with transaction() as db:
        db.get(RecurringRule, uuid.UUID(rule["id"])).enabled = True
        db.scalar(select(User)).disabled_at = utc("2024-01-01T00:00:00")
    assert service.scan_due(utc("2024-01-05T00:00:00")) == 0


def test_duplicate_scan_batch_and_restart(logged_in):
    rule = create(logged_in, body(logged_in, end_on="2024-01-05"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(
            pool.map(lambda _: service.scan_due(utc("2024-01-10T00:00:00"), limit=2), range(2))
        )
    assert sum(counts) in (2, 4)
    # New DB connections are the only state; repeated scans resume the saved cursor.
    engine().dispose()
    while service.scan_due(utc("2024-01-10T00:00:00"), limit=2):
        pass
    assert len(occurrences(logged_in, rule)) == 5
    while run_once():
        pass
    rows = occurrences(logged_in, rule)
    assert len({r["transaction_id"] for r in rows}) == 5
    assert all(r["status"] == "posted" and r["actual_verified_at"] is None for r in rows)
    with Session(engine()) as db:
        assert (
            db.scalar(
                select(func.count()).select_from(Transaction).where(Transaction.kind == "expense")
            )
            == 5
        )


def test_process_crash_after_post_before_job_ack(logged_in):
    rule = create(logged_in, body(logged_in, end_on="2024-01-01"))
    service.scan_due(utc("2024-01-02T00:00:00"))
    code = "from app.core.jobs import claim;from app.recurring.service import post_occurrence;import os;j,t,k=claim(['recurring_post']);post_occurrence(j,t);os._exit(37)"
    result = subprocess.run([sys.executable, "-c", code], env=os.environ.copy())
    assert result.returncode == 37
    first = occurrences(logged_in, rule)[0]["transaction_id"]
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.kind == "recurring_post"))
        old_token, job_id = job.lease_token, job.id
        job.lease_until = utc("2024-01-01T00:00:00")
    assert run_once()
    assert not finish(job_id, old_token)
    assert occurrences(logged_in, rule)[0]["transaction_id"] == first
    assert service.scan_due(utc("2024-01-02T00:00:00")) == 0


def test_failure_rolls_back_and_retry_posts_once(logged_in, monkeypatch):
    rule = create(logged_in, body(logged_in, end_on="2024-01-01"))
    service.scan_due(utc("2024-01-02T00:00:00"))
    original = service.ledger.create_transaction

    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic crash before commit")

    monkeypatch.setattr(service.ledger, "create_transaction", fail_after_write)
    assert run_once()
    assert occurrences(logged_in, rule)[0]["transaction_id"] is None
    with Session(engine()) as db:
        assert (
            db.scalar(
                select(func.count()).select_from(Transaction).where(Transaction.kind == "expense")
            )
            == 0
        )
    monkeypatch.setattr(service.ledger, "create_transaction", original)
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.kind == "recurring_post"))
        assert job.status == "retry_wait"
        job.run_after = utc("2024-01-01T00:00:00")
    assert run_once()
    assert not run_once()
    assert occurrences(logged_in, rule)[0]["status"] == "posted"


def test_revision_frozen_pending_and_future_only(logged_in, clock):
    payload = body(logged_in, end_on="2024-01-03")
    rule = create(logged_in, payload)
    assert create(logged_in, payload, "repeat")["id"] == create(logged_in, payload, "repeat")["id"]
    clock[0] = utc("2024-01-01T12:00:00")
    change = {
        **payload,
        "template": {**payload["template"], "amount": "20"},
        "expected_revision": 1,
    }
    blocked = logged_in.put(f"/api/v1/recurring-rules/{rule['id']}", json=change)
    assert blocked.status_code == 409
    service.scan_due(clock[0])
    updated = logged_in.put(f"/api/v1/recurring-rules/{rule['id']}", json=change)
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    assert logged_in.put(f"/api/v1/recurring-rules/{rule['id']}", json=change).status_code == 409
    service.scan_due(utc("2024-01-04T00:00:00"))
    while run_once():
        pass
    rows = occurrences(logged_in, rule)
    assert [r["rule_revision"] for r in rows] == [1, 2, 2]
    assert [float(r["expected_amount"]) for r in rows] == [10, 20, 20]
    assert service.scan_due(utc("2024-01-04T00:00:00")) == 0


def test_disable_cancels_queued_post_and_reenable_skips_disabled_window(logged_in, clock):
    payload = body(logged_in)
    rule = create(logged_in, payload)
    clock[0] = utc("2024-01-01T12:00:00")
    service.scan_due(clock[0])
    response = logged_in.put(
        f"/api/v1/recurring-rules/{rule['id']}",
        json={**payload, "enabled": False, "expected_revision": 1},
    )
    assert response.status_code == 200
    assert run_once()
    assert occurrences(logged_in, rule)[0]["status"] == "skipped"
    clock[0] = utc("2024-01-04T12:00:00")
    response = logged_in.put(
        f"/api/v1/recurring-rules/{rule['id']}", json={**payload, "expected_revision": 2}
    )
    assert response.status_code == 200
    assert service.scan_due(clock[0]) == 0
    assert service.scan_due(utc("2024-01-05T09:00:00")) == 1


def test_stale_lease_has_no_effect(logged_in):
    rule = create(logged_in, body(logged_in, end_on="2024-01-01"))
    service.scan_due(utc("2024-01-02T00:00:00"))
    job, token, _ = claim(["recurring_post"])
    with pytest.raises(RuntimeError, match="lease_lost"):
        service.post_occurrence(job, uuid.uuid4())
    assert occurrences(logged_in, rule)[0]["transaction_id"] is None
    service.post_occurrence(job, token)
    service.post_occurrence(job, token)
    assert finish(job, token)


@pytest.mark.parametrize(
    "changes",
    [
        {"timezone": "Invalid/Nowhere"},
        {"interval": 0},
        {"frequency": "yearly"},
        {"end_on": "2023-12-31"},
        {"local_time": "09:00:00+08:00"},
    ],
)
def test_invalid_schedule(logged_in, changes):
    payload = body(logged_in)
    assert (
        logged_in.post(
            "/api/v1/recurring-rules",
            json={**payload, **changes},
            headers={"Idempotency-Key": "invalid"},
        ).status_code
        == 422
    )


def test_backup_restore_recurring_snapshots_and_pending_jobs(logged_in, tmp_path):
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from app.core.backup import create_backup, recurring_fingerprint, restore_backup, verify_backup
    from app.core.config import settings

    rule = create(logged_in, body(logged_in, end_on="2024-01-02"))
    service.scan_due(utc("2024-01-01T09:00:00"))
    assert run_once()
    service.scan_due(utc("2024-01-02T09:00:00"))
    first = occurrences(logged_in, rule)[0]["transaction_id"]
    path = create_backup()
    manifest = verify_backup(path)
    assert manifest["counts"]["recurring_rules"] == 1
    assert manifest["counts"]["recurring_occurrences"] == 2
    url = make_url(settings().database_url.get_secret_value())
    name = "finance_restore_" + uuid.uuid4().hex
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    target = url.set(database=name).render_as_string(hide_password=False)
    restored = create_engine(target)
    try:
        restore_backup(path, target, tmp_path / "restored")
        with restored.connect() as conn:
            assert recurring_fingerprint(conn) == manifest["recurring_hash"]
        code = """
from datetime import UTC,datetime
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from app.core.db import engine
from app.core.models import RecurringOccurrence,Transaction
from app.recurring.service import scan_due
from app.worker import run_once
assert scan_due(datetime(2024,1,3,tzinfo=UTC)) == 0
assert run_once()
assert not run_once()
with Session(engine()) as db:
    rows=list(db.scalars(select(RecurringOccurrence).order_by(RecurringOccurrence.scheduled_local_date)))
    assert len(rows)==2 and all(row.status=='posted' for row in rows)
    assert str(rows[0].transaction_id)==FIRST
    assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.kind=='expense'))==2
""".replace("FIRST", repr(first))
        subprocess.run(
            [sys.executable, "-c", code], env={**os.environ, "DATABASE_URL": target}, check=True
        )
    finally:
        restored.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()


def test_disable_then_reenable_never_revives_old_job(logged_in, clock):
    payload = body(logged_in)
    rule = create(logged_in, payload)
    clock[0] = utc("2024-01-01T12:00:00")
    service.scan_due(clock[0])
    for revision, enabled in [(1, False), (2, True)]:
        response = logged_in.put(
            f"/api/v1/recurring-rules/{rule['id']}",
            json={**payload, "enabled": enabled, "expected_revision": revision},
        )
        assert response.status_code == 200
    assert run_once()
    assert occurrences(logged_in, rule)[0]["status"] == "skipped"
    assert occurrences(logged_in, rule)[0]["transaction_id"] is None


def test_api_owner_and_csrf(logged_in):
    rule = create(logged_in, body(logged_in))
    response = logged_in.get(f"/api/v1/recurring-rules/{uuid.uuid4()}/occurrences")
    assert response.status_code == 404
    response = logged_in.put(
        f"/api/v1/recurring-rules/{rule['id']}", json={}, headers={"X-CSRF-Token": "wrong"}
    )
    assert response.status_code == 403
    from conftest import PASSWORD

    from app.core.models import BookSettings, User

    with transaction() as db:
        alice = db.scalar(select(User))
        bob = User(login_name="bob", password_hash=alice.password_hash)
        db.add(bob)
        db.flush()
        db.add(BookSettings(owner_id=bob.id))
    assert (
        logged_in.post(
            "/api/v1/auth/login", json={"username": "bob", "password": PASSWORD}
        ).status_code
        == 200
    )
    assert logged_in.get(f"/api/v1/recurring-rules/{rule['id']}/occurrences").status_code == 404
    assert logged_in.get("/api/v1/recurring-rules").json() == []


def test_template_validation_rejects_foreign_account_and_incomplete_split(logged_in):
    payload = body(logged_in)
    template = {**payload["template"], "account_id": str(uuid.uuid4())}
    assert (
        logged_in.post(
            "/api/v1/recurring-rules",
            json={**payload, "template": template},
            headers={"Idempotency-Key": "foreign-account"},
        ).status_code
        == 404
    )
    template = {
        **payload["template"],
        "category_id": None,
        "splits": [{"category_id": payload["template"]["category_id"], "amount": "1"}],
    }
    assert (
        logged_in.post(
            "/api/v1/recurring-rules",
            json={**payload, "template": template},
            headers={"Idempotency-Key": "bad-split"},
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "anchor,cutoff,expected",
    [
        (
            "2024-03-09",
            "2024-03-12T00:00:00",
            ["2024-03-09T10:30:00", "2024-03-10T10:00:00", "2024-03-11T09:30:00"],
        ),
        (
            "2024-11-02",
            "2024-11-05T00:00:00",
            ["2024-11-02T08:30:00", "2024-11-03T08:30:00", "2024-11-04T09:30:00"],
        ),
    ],
)
def test_daily_dst_does_not_turn_into_24_hour_utc_interval(logged_in, anchor, cutoff, expected):
    clock = "02:30:00" if "03-" in anchor else "01:30:00"
    rule = create(
        logged_in,
        body(
            logged_in,
            anchor_date=anchor,
            local_time=clock,
            timezone="America/Los_Angeles",
            posting_mode="expect_only",
        ),
    )
    assert service.scan_due(utc(cutoff)) == 3
    assert [datetime.fromisoformat(r["scheduled_at"]) for r in occurrences(logged_in, rule)] == [
        utc(v) for v in expected
    ]


def test_shortened_end_cancels_pending_after_end(logged_in, clock):
    payload = body(logged_in, end_on="2024-01-04")
    rule = create(logged_in, payload)
    clock[0] = utc("2024-01-03T12:00:00")
    service.scan_due(clock[0])
    response = logged_in.put(
        f"/api/v1/recurring-rules/{rule['id']}",
        json={**payload, "end_on": "2024-01-02", "expected_revision": 1},
    )
    assert response.status_code == 200
    while run_once():
        pass
    assert [r["status"] for r in occurrences(logged_in, rule)] == ["posted", "posted", "skipped"]


def test_cursor_rewind_unique_key_cannot_repost(logged_in):
    rule = create(logged_in, body(logged_in, end_on="2024-01-01"))
    service.scan_due(utc("2024-01-02T00:00:00"))
    assert run_once()
    original = occurrences(logged_in, rule)[0]["transaction_id"]
    with transaction() as db:
        row = db.get(RecurringRule, uuid.UUID(rule["id"]))
        service.set_cursor(row, row.anchor_date)
    assert service.scan_due(utc("2024-01-02T00:00:00")) == 1
    assert not run_once()
    assert occurrences(logged_in, rule)[0]["transaction_id"] == original


def test_expired_lease_after_ledger_write_rolls_back(logged_in, monkeypatch):
    rule = create(logged_in, body(logged_in, end_on="2024-01-01"))
    service.scan_due(utc("2024-01-02T00:00:00"))
    job_id, token, _ = claim(["recurring_post"])
    with Session(engine()) as db:
        deadline = db.get(Job, job_id).lease_until
    original = service.ledger.create_transaction

    def expire_after_write(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(service, "now", lambda: deadline + timedelta(seconds=1))
        return result

    monkeypatch.setattr(service.ledger, "create_transaction", expire_after_write)
    with pytest.raises(RuntimeError, match="lease_lost"):
        service.post_occurrence(job_id, token)
    assert occurrences(logged_in, rule)[0]["transaction_id"] is None
    with Session(engine()) as db:
        assert (
            db.scalar(
                select(func.count()).select_from(Transaction).where(Transaction.kind == "expense")
            )
            == 0
        )
