from datetime import date, datetime, time
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.schemas import StrictModel
from app.ledger.schemas import TransactionInput


class RuleInput(StrictModel):
    template: TransactionInput
    frequency: Literal["daily", "weekly", "monthly"]
    interval: int = Field(default=1, ge=1, le=120)
    anchor_date: date
    local_time: time
    timezone: str = Field(max_length=100)
    end_on: date | None = None
    posting_mode: Literal["auto_post", "expect_only"]
    enabled: bool = True

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("請使用有效 IANA 時區") from None
        return value

    @model_validator(mode="after")
    def valid_schedule(self) -> "RuleInput":
        if self.local_time.tzinfo or self.local_time.microsecond:
            raise ValueError("local_time 必須是不含時區的整秒時間")
        if self.end_on and self.end_on < self.anchor_date:
            raise ValueError("結束日期不得早於錨點")
        return self


class RuleUpdate(RuleInput):
    expected_revision: int = Field(ge=1)


class RuleOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    template: dict
    frequency: str
    interval: int
    anchor_date: date
    local_time: str
    timezone: str
    end_on: date | None
    next_local_date: date | None
    next_due_at: datetime | None
    posting_mode: str
    enabled: bool
    revision: int


class OccurrenceOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    rule_id: UUID
    scheduled_local_date: date
    scheduled_at: datetime
    expected_amount: str
    currency: str
    status: str
    transaction_id: UUID | None
    actual_verified_at: datetime | None
    rule_revision: int
    posting_mode: str

    @field_validator("expected_amount", mode="before")
    @classmethod
    def amount_string(cls, value: object) -> str:
        return str(value)
