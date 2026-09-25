from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.schemas import StrictModel
from app.ledger.schemas import Money, SplitInput


class ParsedItem(StrictModel):
    raw_name: str = Field(max_length=500)
    quantity: Money | None
    unit_price: Money | None
    line_total: Money | None


class ParsedReceipt(StrictModel):
    merchant: str | None = Field(max_length=200)
    occurred_on: date | None
    currency: str | None = Field(max_length=12)
    amount: Money | None
    subtotal: Money | None
    tax: Money | None
    tip: Money | None
    discount: Money | None
    items: list[ParsedItem] = Field(max_length=200)
    uncertainty: str | None = Field(max_length=1000)


class Proposal(StrictModel):
    kind: Literal["expense", "income"] = "expense"
    occurred_on: date | None = None
    currency: Literal["USD", "TWD"] | None = None
    amount: Money | None = None
    account_id: UUID | None = None
    category_id: UUID | None = None
    splits: list[SplitInput] = Field(default_factory=list, max_length=30)
    merchant: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    fx_rate: Money | None = None


class DraftEdit(StrictModel):
    expected_revision: int = Field(ge=1)
    proposal: Proposal


class DraftAction(StrictModel):
    expected_revision: int = Field(ge=1)
    acknowledge_warnings: bool = False


class TextCapture(StrictModel):
    text: str = Field(min_length=1, max_length=2000)


class DraftOutput(BaseModel):
    id: UUID
    revision: int
    status: str
    source: str
    proposal: Proposal
    warnings: list[str]
    parsed: ParsedReceipt | None
    attachment_id: UUID | None
    confirmed_transaction_id: UUID | None
    created_at: datetime
    job_status: str | None
    error_code: str | None


class CaptureStatus(BaseModel):
    provider: str
    model: str
    telegram_configured: bool
    bridge_last_seen: datetime | None
    telegram_last_received: datetime | None


class TelegramUpdate(StrictModel):
    update_id: int = Field(ge=0)
    bot_id: int = Field(gt=0)
    user_id: int | None = None
    chat_id: int | None = None
    private: bool = False
    message_id: int | None = None
    text: str = Field(default="", max_length=4096)
    file_id: str | None = Field(default=None, max_length=512)
    filename: str = Field(default="receipt.jpg", max_length=200)
    callback: str = Field(default="", max_length=64)


class BridgeClaim(StrictModel):
    kinds: list[Literal["capture_parse", "telegram_download", "telegram_send"]] = Field(
        min_length=1, max_length=3
    )


class BridgeJob(BaseModel):
    heartbeat_seconds: float
    id: UUID
    lease_token: UUID
    kind: str
    payload: dict[str, Any]


class BridgeResult(StrictModel):
    lease_token: UUID
    parsed: ParsedReceipt | None = None
    error_code: Annotated[str, Field(pattern=r"^[a-z_]{1,60}$")] | None = None
    retry_after: int = Field(default=0, ge=0, le=86400)
