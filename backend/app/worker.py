import logging
import signal
import threading
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select, text

from app.core.backup import create_backup
from app.core.config import settings
from app.core.db import SCHEDULER_LOCK, check_schema, transaction
from app.core.jobs import apply_probe, claim, dispatch_outbox, enqueue, finish, heartbeat
from app.core.models import BackupRun, User, now
from app.receipts.service import collect_garbage

log = logging.getLogger("finance.worker")
stop = threading.Event()


def schedule_backup() -> None:
    # Transaction-scoped leader lock: only one scheduler scans at a time.
    with transaction() as db:
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": SCHEDULER_LOCK}):
            return
        owner = db.scalar(
            select(User).where(User.disabled_at.is_(None)).order_by(User.created_at).limit(1)
        )
        if not owner:
            return
        latest = db.scalar(
            select(BackupRun)
            .where(BackupRun.status == "succeeded")
            .order_by(BackupRun.completed_at.desc())
            .limit(1)
        )
        if not latest or not latest.completed_at or latest.completed_at < now() - timedelta(days=1):
            enqueue(db, owner.id, "backup", f"daily-backup:{now().date().isoformat()}", {})


def run_once() -> bool:
    dispatch_outbox()
    claimed = claim()
    if not claimed:
        return False
    job_id, token, kind = claimed
    stopped = threading.Event()

    def keep_alive() -> None:
        while not stopped.wait(settings().lease_seconds / 3):
            try:
                if not heartbeat(job_id, token):
                    return
            except Exception:
                log.warning("job_heartbeat_failed job_id=%s", job_id)
                return

    thread = threading.Thread(target=keep_alive, daemon=True)
    thread.start()
    try:
        if kind in {"probe", "event_ack"}:
            apply_probe(job_id, token)
        elif kind == "backup":
            create_backup(job_id)
        else:
            raise ValueError("unknown_handler")
        finish(job_id, token)
    except Exception:
        log.warning("job_failed job_id=%s", job_id)
        finish(job_id, token, "handler_failed")
    finally:
        stopped.set()
        thread.join(timeout=2)
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    check_schema()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(collect_garbage, "interval", minutes=5, coalesce=True, max_instances=1)
    scheduler.add_job(
        schedule_backup,
        "interval",
        seconds=60,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
        next_run_time=now(),
    )
    scheduler.start()
    try:
        while not stop.is_set():
            try:
                if not run_once():
                    stop.wait(1)
            except Exception:
                log.warning("worker_database_unavailable")
                stop.wait(5)
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
