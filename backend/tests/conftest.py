import os
import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from alembic import command

# Never point these tests at a user's real database.
URL = os.environ.get("TEST_DATABASE_URL", "")
if not URL or not (make_url(URL).database or "").startswith("finance_test"):
    raise RuntimeError(
        "Set TEST_DATABASE_URL to a disposable PostgreSQL database named finance_test..."
    )
os.environ["DATABASE_URL"] = URL
os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))
os.environ.setdefault("TOTP_KEY", Fernet.generate_key().decode())
os.environ["APP_ORIGIN"] = "http://localhost:5173"
os.environ["COOKIE_SECURE"] = "false"

from app.cli import initialize_user  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.db import engine  # noqa: E402
from app.core.models import Base  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = "synthetic-test-passphrase"


@pytest.fixture(scope="session", autouse=True)
def migrated() -> Iterator[None]:
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(cfg, "head")
    yield
    engine().dispose()


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path) -> None:
    os.environ["DATA_DIR"] = str(tmp_path / "data")
    os.environ["BACKUP_DIR"] = str(tmp_path / "backups")
    (tmp_path / "data").mkdir()
    settings.cache_clear()
    tables = [t.name for t in Base.metadata.sorted_tables if t.name != "currencies"]
    with engine().begin() as conn:
        conn.execute(text("TRUNCATE " + ",".join(f'"{name}"' for name in tables) + " CASCADE"))
    initialize_user("alice", PASSWORD)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, base_url="http://localhost:5173") as client:
        token = client.get("/api/v1/auth/csrf").json()["token"]
        client.headers.update({"Origin": "http://localhost:5173", "X-CSRF-Token": token})
        yield client


@pytest.fixture
def logged_in(client: TestClient) -> TestClient:
    response = client.post("/api/v1/auth/login", json={"username": "alice", "password": PASSWORD})
    assert response.status_code == 200
    return client
