import uuid

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Principal, audit, principal
from app.core.db import engine, transaction
from app.core.models import RecurringOccurrence, RecurringRule
from app.ledger import service as ledger
from app.ledger.routes import protect_writes
from app.recurring.schemas import OccurrenceOutput, RuleInput, RuleOutput, RuleUpdate
from app.recurring.service import save_rule

router = APIRouter(prefix="/api/v1/recurring-rules", dependencies=[Depends(protect_writes)])


@router.get("", response_model=list[RuleOutput])
def rules(user: Principal = Depends(principal)) -> list[RuleOutput]:
    with Session(engine()) as db:
        return [
            RuleOutput.model_validate(row)
            for row in db.scalars(
                select(RecurringRule)
                .where(RecurringRule.owner_id == user.owner_id)
                .order_by(RecurringRule.created_at, RecurringRule.id)
            )
        ]


@router.post("", response_model=RuleOutput)
def create(
    data: RuleInput,
    request: Request,
    key: str = Header(alias="Idempotency-Key"),
    user: Principal = Depends(principal),
) -> RuleOutput:
    with transaction() as db:
        ledger.book_lock(db, user.owner_id)
        previous, digest = ledger.idempotent(db, user.owner_id, "recurring.create", key, data)
        row = (
            ledger.owned(db, RecurringRule, user.owner_id, previous)
            if previous
            else save_rule(db, user.owner_id, data)
        )
        if not previous:
            ledger.remember(db, user.owner_id, "recurring.create", key, digest, row.id)
            audit(db, user.owner_id, "recurring.create", str(row.id), request)
        return RuleOutput.model_validate(row)


@router.put("/{identity}", response_model=RuleOutput)
def update(
    identity: uuid.UUID, data: RuleUpdate, request: Request, user: Principal = Depends(principal)
) -> RuleOutput:
    with transaction() as db:
        row = save_rule(db, user.owner_id, data, identity, data.expected_revision)
        audit(db, user.owner_id, "recurring.update", str(row.id), request)
        return RuleOutput.model_validate(row)


@router.get("/{identity}/occurrences", response_model=list[OccurrenceOutput])
def occurrences(
    identity: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    user: Principal = Depends(principal),
) -> list[OccurrenceOutput]:
    with Session(engine()) as db:
        ledger.owned(db, RecurringRule, user.owner_id, identity)
        return [
            OccurrenceOutput.model_validate(row)
            for row in db.scalars(
                select(RecurringOccurrence)
                .where(
                    RecurringOccurrence.owner_id == user.owner_id,
                    RecurringOccurrence.rule_id == identity,
                )
                .order_by(RecurringOccurrence.scheduled_local_date.desc())
                .offset(offset)
                .limit(limit)
            )
        ]
