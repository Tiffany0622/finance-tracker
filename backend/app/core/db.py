from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.core.config import settings

MAINTENANCE_LOCK = 724801
SCHEDULER_LOCK = 724802
SCHEMA_VERSION = "0002_ledger"


@lru_cache
def engine() -> Engine:
    return create_engine(
        settings().database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


@contextmanager
def transaction() -> Iterator[Session]:
    """All application writers share the lock; backups take it exclusively."""
    with Session(engine()) as db, db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": MAINTENANCE_LOCK})
        yield db


def check_schema() -> None:
    with engine().connect() as conn:
        version = conn.scalar(text("SELECT version_num FROM alembic_version"))
        if version != SCHEMA_VERSION:
            raise RuntimeError("Database migration required")
