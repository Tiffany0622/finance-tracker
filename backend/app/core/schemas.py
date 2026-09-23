from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldIssue(BaseModel):
    field: str
    code: str


class ErrorOutput(BaseModel):
    code: str
    message: str
    field_errors: list[FieldIssue]
    request_id: str


class LoginInput(StrictModel):
    username: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=1, max_length=1024)
    code: str = Field(default="", max_length=64)


class VerifyInput(StrictModel):
    password: SecretStr = Field(min_length=1, max_length=1024)
    code: str = Field(default="", max_length=64)


class SettingsInput(StrictModel):
    book_currency: Literal["USD", "TWD"]
    timezone: str = Field(max_length=100)
    expected_revision: int = Field(ge=0)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("請選擇有效的 IANA 時區") from None
        return value


class SettingsOutput(BaseModel):
    book_currency: str | None
    timezone: str | None
    locale: str
    settings_revision: int
    setup_completed: bool


class UserOutput(BaseModel):
    username: str
    totp_enabled: bool
    settings: SettingsOutput


class JobOutput(BaseModel):
    id: UUID
    kind: str
    status: str
    attempts: int
    run_after: datetime
    error_code: str | None


class MessageOutput(BaseModel):
    message: str


class CsrfOutput(BaseModel):
    token: str


class TotpSetupOutput(BaseModel):
    secret: str
    provisioning_uri: str


class RecoveryOutput(BaseModel):
    recovery_codes: list[str]


class StatusOutput(BaseModel):
    phase: str
    database: str
    disk_free_bytes: int
    disk_low: bool
    backup_last_success: datetime | None
    backup_overdue: bool
    backup_last_error: str | None
    backup_destination: str
    jobs_pending: int
    jobs_failed: int
    integrations: dict[str, str]
