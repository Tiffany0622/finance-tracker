from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.schemas import StrictModel

Money = Annotated[str, Field(pattern=r"^-?\d{1,14}(\.\d{1,18})?$", max_length=34)]


class AccountInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["bank", "cash", "credit_card"]
    currency: Literal["USD", "TWD"]
    opening_balance: Money = "0"
    opening_on: date
    fx_rate: Money | None = None
    include_in_net_worth: bool = True


class AccountUpdate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    include_in_net_worth: bool
    archived: bool
    expected_revision: int = Field(ge=1)


class AccountOutput(BaseModel):
    id: UUID
    name: str
    kind: str
    currency: str
    balance: str
    include_in_net_worth: bool
    archived: bool
    revision: int


class CategoryInput(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["income", "expense"]
    parent_id: UUID | None = None


class CategoryOutput(CategoryInput):
    id: UUID
    archived: bool = False


class SplitInput(StrictModel):
    category_id: UUID
    amount: Money


class TransactionInput(StrictModel):
    kind: Literal["income", "expense", "transfer", "refund"]
    occurred_on: date
    account_id: UUID
    amount: Money
    category_id: UUID | None = None
    splits: list[SplitInput] = Field(default_factory=list, max_length=30)
    to_account_id: UUID | None = None
    received_amount: Money | None = None
    fx_rate: Money | None = None
    received_fx_rate: Money | None = None
    fee: Money = "0"
    fee_category_id: UUID | None = None
    refund_of_id: UUID | None = None
    merchant: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("tags")
    @classmethod
    def tags_valid(cls, values: list[str]) -> list[str]:
        if any(not v.strip() or len(v) > 50 for v in values):
            raise ValueError("標籤需為 1–50 字")
        return list(dict.fromkeys(v.strip() for v in values))

    @model_validator(mode="after")
    def positive(self) -> "TransactionInput":
        if Decimal(self.amount) <= 0 or Decimal(self.fee) < 0:
            raise ValueError("金額必須為正，費用不可負數")
        if self.kind != "transfer" and (
            self.to_account_id
            or self.received_amount
            or Decimal(self.fee)
            or self.fee_category_id
            or self.received_fx_rate
        ):
            raise ValueError("只有轉帳可指定轉入與費用")
        if self.kind != "refund" and self.refund_of_id:
            raise ValueError("只有退款可關聯原交易")
        return self


class TransactionUpdate(TransactionInput):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class VoidInput(StrictModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class TransactionOutput(BaseModel):
    id: UUID
    revision: int
    status: str
    kind: str
    occurred_on: date | None
    account_id: UUID | None
    account: str
    to_account_id: UUID | None
    to_account: str
    amount: str
    currency: str
    book_amount: str
    received_amount: str | None
    fx_rate: str
    received_fx_rate: str | None
    fee: str
    fee_category_id: UUID | None
    splits: list[SplitInput]
    categories: list[str]
    refund_of_id: UUID | None
    merchant: str
    note: str
    tags: list[str]


class TransactionPage(BaseModel):
    items: list[TransactionOutput]
    total: int


class QuoteInput(StrictModel):
    currency: Literal["USD", "TWD"]
    effective_on: date
    rate: Money
    source: str = Field(min_length=1, max_length=200)


class ReportInput(StrictModel):
    start: date
    end_exclusive: date
    account_id: UUID | None = None
    category_id: UUID | None = None
    query: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def valid_period(self) -> "ReportInput":
        if not 0 < (self.end_exclusive - self.start).days <= 3660:
            raise ValueError("報表期間需為 1 天至 10 年")
        return self


class ReportOutput(BaseModel):
    id: UUID
    document: dict[str, Any]
