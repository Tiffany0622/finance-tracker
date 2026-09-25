import hashlib
import json
import random
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.auth import ApiError
from app.core.config import settings
from app.core.db import engine, transaction
from app.core.models import IdempotencyRecord, Job, JobEffect, OutboxEvent, now

MAX_ATTEMPTS = 5


def enqueue(
    db: Session, owner: uuid.UUID, kind: str, key: str, payload: dict[str, Any]
) -> uuid.UUID:
    result = db.scalar(
        insert(Job)
        .values(owner_id=owner, kind=kind, logical_key=key, payload=payload)
        .on_conflict_do_nothing(index_elements=[Job.owner_id, Job.kind, Job.logical_key])
        .returning(Job.id)
    )
    if result is not None:
        return result
    existing = db.scalar(
        select(Job.id).where(Job.owner_id == owner, Job.kind == kind, Job.logical_key == key)
    )
    assert existing
    return existing


def request_probe(db: Session, owner: uuid.UUID, key: str) -> uuid.UUID:
    payload = {"kind": "probe"}
    hashed = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    # The caller locks the owner row, serializing an owner's idempotency requests.
    previous = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.owner_id == owner,
            IdempotencyRecord.operation == "probe",
            IdempotencyRecord.key == key,
        )
    )
    if previous:
        if previous.request_hash != hashed:
            raise ApiError(409, "idempotency_conflict", "相同請求識別碼的內容不同。")
        return previous.resource_id
    job_id = enqueue(db, owner, "probe", f"probe:{key}", payload)
    db.add(
        IdempotencyRecord(
            owner_id=owner,
            operation="probe",
            key=key,
            request_hash=hashed,
            resource_id=job_id,
            response_code=202,
        )
    )
    db.add(
        OutboxEvent(
            owner_id=owner,
            event_type="probe.requested",
            entity_id=job_id,
            entity_revision=1,
            payload={},
        )
    )
    return job_id


def dispatch_outbox() -> None:
    with transaction() as db:
        events = db.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None))
            .order_by(OutboxEvent.created_at)
            .limit(20)
            .with_for_update(skip_locked=True)
        )
        for event in events:
            if event.event_type == "probe.requested":
                enqueue(
                    db,
                    event.owner_id,
                    "event_ack",
                    f"event:{event.id}",
                    {"event_id": str(event.id)},
                )
                event.published_at = now()
            # Unknown events stay visible; never acknowledge an unimplemented consumer.


def claim(
    kinds: list[str] | None = None, owner: uuid.UUID | None = None
) -> tuple[uuid.UUID, uuid.UUID, str] | None:
    with transaction() as db:
        due = or_(
            and_(Job.status.in_(["queued", "retry_wait"]), Job.run_after <= now()),
            and_(Job.status == "running", Job.lease_until <= now()),
        )
        job = db.scalar(
            select(Job)
            .where(
                due,
                Job.kind.in_(kinds)
                if kinds
                else Job.kind.not_in(["capture_parse", "telegram_download", "telegram_send"]),
                Job.owner_id == owner if owner else true(),
            )
            .order_by(Job.run_after, Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if not job:
            return None
        if job.attempts >= MAX_ATTEMPTS:
            job.status, job.error_code = "failed", "attempts_exhausted"
            job.lease_token, job.lease_until = None, None
            return None
        job.status, job.lease_token = "running", uuid.uuid4()
        job.attempts += 1
        job.heartbeat_at = now()
        job.lease_until = now() + timedelta(seconds=settings().lease_seconds)
        return job.id, job.lease_token, job.kind


def heartbeat(job_id: uuid.UUID, token: uuid.UUID) -> bool:
    # Operational lease metadata is allowed during backup; never touches financial rows.
    with Session(engine()) as db, db.begin():
        result = db.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.lease_token == token,
                Job.status == "running",
                Job.lease_until > now(),
            )
            .values(
                heartbeat_at=now(), lease_until=now() + timedelta(seconds=settings().lease_seconds)
            )
        )
        return result.rowcount == 1  # type: ignore[attr-defined,no-any-return]


def finish(job_id: uuid.UUID, token: uuid.UUID, error: str | None = None) -> bool:
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if (
            not job
            or job.status != "running"
            or job.lease_token != token
            or not job.lease_until
            or job.lease_until <= now()
        ):
            return False
        if error:
            job.error_code = error
            job.status = "failed" if job.attempts >= MAX_ATTEMPTS else "retry_wait"
            job.run_after = now() + timedelta(seconds=min(300, 2**job.attempts) + random.random())
        else:
            job.status, job.error_code = "succeeded", None
        job.lease_token, job.lease_until = None, None
        return True


def apply_probe(job_id: uuid.UUID, token: uuid.UUID) -> None:
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if (
            not job
            or job.lease_token != token
            or job.status != "running"
            or not job.lease_until
            or job.lease_until <= now()
        ):
            raise RuntimeError("lease_lost")
        db.execute(insert(JobEffect).values(job_id=job_id).on_conflict_do_nothing())
