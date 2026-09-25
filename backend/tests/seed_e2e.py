"""Destructive synthetic browser fixture. Explicitly refuses any production database."""

import os

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.cli import initialize_user
from app.core.db import engine
from app.core.models import Base

url = os.environ.get("TEST_DATABASE_URL", "")
if (
    not url
    or url != os.environ.get("DATABASE_URL")
    or not (make_url(url).database or "").startswith("finance_test")
):
    raise SystemExit("Both DB URLs must name the same disposable finance_test database")
with engine().begin() as conn:
    names = [table.name for table in Base.metadata.sorted_tables if table.name != "currencies"]
    conn.execute(text("TRUNCATE " + ",".join('"' + name + '"' for name in names) + " CASCADE"))
initialize_user("alice", "synthetic-test-passphrase")
print("Synthetic browser fixture ready.")
