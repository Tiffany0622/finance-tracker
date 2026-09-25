from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    database_url: SecretStr
    jwt_secret: SecretStr
    totp_key: SecretStr
    app_origin: str = "http://localhost:8080"
    cookie_secure: bool = False
    data_dir: Path = Path("/data")
    backup_dir: Path = Path("/backups")
    app_commit: str = "development"
    access_minutes: int = Field(default=15, ge=1, le=60)
    session_days: int = Field(default=7, ge=1, le=30)
    capture_provider: Literal["disabled", "ollama", "openai"] = "disabled"
    capture_model: str = Field(default="", max_length=100)
    telegram_bot_id: int = Field(default=0, ge=0)
    telegram_user_id: int = Field(default=0, ge=0)
    lease_seconds: int = Field(default=60, ge=3, le=3600)

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        if not self.database_url.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use postgresql+psycopg")
        if len(self.jwt_secret.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must have at least 32 characters")
        Fernet(self.totp_key.get_secret_value().encode())
        origin = urlsplit(self.app_origin)
        if origin.path or origin.query or origin.fragment or origin.username:
            raise ValueError("APP_ORIGIN must contain only scheme and host/port")
        if origin.scheme not in {"http", "https"} or not origin.hostname:
            raise ValueError("Invalid APP_ORIGIN")
        if not self.cookie_secure and origin.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Non-loopback access requires secure HTTPS cookies")
        if self.cookie_secure and origin.scheme != "https":
            raise ValueError("Secure cookies require HTTPS")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
