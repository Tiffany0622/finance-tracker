import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import PASSWORD
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.backup import create_backup, prune_backups, restore_backup, verify_backup
from app.core.config import settings
from app.core.db import MAINTENANCE_LOCK, engine, transaction
from app.core.jobs import claim, dispatch_outbox, enqueue, finish, heartbeat
from app.core.models import BackupRun, Job, JobEffect, OutboxEvent, User, now
from app.core.security import password_ok
from app.worker import run_once, schedule_backup


def test_duplicate_api_request_and_outbox(logged_in: TestClient) -> None:
    one = logged_in.post("/api/v1/jobs/probe", headers={"Idempotency-Key": "same-request-123"})
    two = logged_in.post("/api/v1/jobs/probe", headers={"Idempotency-Key": "same-request-123"})
    assert one.status_code == two.status_code == 202
    assert one.json()["id"] == two.json()["id"]
    dispatch_outbox()
    dispatch_outbox()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 1
        assert db.scalar(select(func.count()).select_from(Job)) == 2
    assert run_once() and run_once()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(JobEffect)) == 2


def add_job(kind: str = "probe") -> uuid.UUID:
    with transaction() as db:
        owner = db.scalar(select(User.id))
        assert owner
        return enqueue(db, owner, kind, str(uuid.uuid4()), {})


def test_competing_workers_claim_one_job() -> None:
    job_id = add_job()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    claimed = [x for x in results if x]
    assert len(claimed) == 1 and claimed[0][0] == job_id


def test_process_crash_after_effect_is_recovered_once() -> None:
    job_id = add_job()
    code = "from app.core.jobs import claim,apply_probe;import os; j,t,k=claim(); apply_probe(j,t); os._exit(37)"
    result = subprocess.run([sys.executable, "-c", code], env=os.environ.copy())
    assert result.returncode == 37
    with transaction() as db:
        job = db.get(Job, job_id)
        assert job
        stale_token = job.lease_token
        job.lease_until = now() - timedelta(seconds=1)
    assert run_once()
    assert stale_token and not finish(job_id, stale_token)
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(JobEffect)) == 1
        job = db.get(Job, job_id)
        assert job
        assert job.status == "succeeded" and job.attempts == 2


def test_retry_limit_and_heartbeat_during_backup_lock() -> None:
    job_id = add_job("unsupported")
    item = claim()
    assert item
    with engine().connect() as lock:
        lock.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MAINTENANCE_LOCK})
        try:
            assert heartbeat(item[0], item[1])
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MAINTENANCE_LOCK})
    assert finish(item[0], item[1], "synthetic_failure")
    for _ in range(4):
        with transaction() as db:
            job = db.get(Job, job_id)
            assert job
            job.run_after = now() - timedelta(seconds=1)
        assert run_once()
    with Session(engine()) as db:
        job = db.get(Job, job_id)
        assert job
        assert job.status == "failed" and job.attempts == 5


def test_missed_schedule_is_enqueued_once() -> None:
    schedule_backup()
    schedule_backup()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "backup")) == 1


def test_backup_restore_verifies_rows_password_and_attachments(
    tmp_path: Path, logged_in: TestClient
) -> None:
    from test_ledger import expense, post, report, setup

    from app.core.models import ReportSnapshot
    from app.ledger.service import balance_accounts

    bank, card, _, food, _, _ = setup(logged_in)
    purchase = post(logged_in, "/transactions", expense(card, food))
    post(
        logged_in,
        "/transactions",
        dict(
            kind="transfer",
            occurred_on="2026-01-06",
            account_id=bank["id"],
            to_account_id=card["id"],
            amount="100",
        ),
    )
    post(
        logged_in,
        "/transactions",
        dict(
            kind="refund",
            occurred_on="2026-01-07",
            account_id=card["id"],
            amount="20",
            refund_of_id=purchase["id"],
        ),
    )
    saved_report = report(logged_in)
    source = settings().data_dir / "attachments"
    source.mkdir()
    (source / "synthetic.txt").write_text("虛構測試收據，不含個人資料")
    backup_id = uuid.uuid4()
    path = create_backup(backup_id)
    with Session(engine()) as db:
        first = db.get(BackupRun, backup_id)
        assert first
        completed_at = first.completed_at
    assert create_backup(backup_id) == path
    with Session(engine()) as db:
        repeated = db.get(BackupRun, backup_id)
        assert repeated and repeated.completed_at == completed_at
    verify_backup(path)
    target_db = "finance_restore_" + uuid.uuid4().hex
    url = make_url(settings().database_url.get_secret_value())
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    target_url = url.set(database=target_db).render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{target_db}"'))
    restored = create_engine(target_url)
    try:
        restore_backup(path, target_url, tmp_path / "restored")
        with Session(restored) as db:
            user = db.scalar(select(User))
            assert user and user.login_name == "alice"
            assert password_ok(user.password_hash, PASSWORD)
            assert {a.name: a.balance for a in balance_accounts(db, user.id)} == {
                "銀行": "900",
                "信用卡": "-20",
                "現金": "0",
            }
            restored_report = db.get(ReportSnapshot, uuid.UUID(saved_report["id"]))
            assert restored_report and restored_report.document == saved_report["document"]
        check_login = """
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app, base_url='http://localhost:5173') as client:
    csrf = client.get('/api/v1/auth/csrf').json()['token']
    result = client.post('/api/v1/auth/login', headers={'Origin':'http://localhost:5173','X-CSRF-Token':csrf},
                         json={'username':'alice','password':'synthetic-test-passphrase'})
    assert result.status_code == 200
    assert client.get('/api/v1/auth/me').json()['username'] == 'alice'
"""
        subprocess.run(
            [sys.executable, "-c", check_login],
            env={**os.environ, "DATABASE_URL": target_url},
            check=True,
        )
        assert (tmp_path / "restored/attachments/synthetic.txt").read_text() == (
            source / "synthetic.txt"
        ).read_text()
        with pytest.raises(ValueError, match="empty"):
            restore_backup(path, target_url, tmp_path / "second")
        with Session(engine()) as db:
            record = db.get(BackupRun, backup_id)
            assert record and record.last_restore_verified_at
        (path / "attachments/synthetic.txt").write_text("tampered")
        with pytest.raises(ValueError, match="checksum"):
            verify_backup(path)
    finally:
        restored.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{target_db}"'))
        admin.dispose()


def test_restore_refuses_live_database(tmp_path: Path) -> None:
    path = create_backup()
    with pytest.raises(ValueError, match="separate"):
        restore_backup(path, settings().database_url.get_secret_value(), tmp_path / "copy")


def test_retention_keeps_latest_and_removes_only_old_complete_backup() -> None:
    old = create_backup()
    recent = create_backup()
    with transaction() as db:
        row = db.get(BackupRun, uuid.UUID(old.name))
        assert row
        row.completed_at = now() - timedelta(days=31)
    prune_backups()
    assert not old.exists()
    assert recent.exists()
    verify_backup(recent)
