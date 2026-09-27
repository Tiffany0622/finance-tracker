import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_capture import PARSED, action, tg
from test_ledger import balances, setup
from test_receipt_items import item_state, ready, save, search
from test_telegram_review import sent

from app.core.db import engine, transaction
from app.core.models import BookSettings, Product, ProductAlias, ReceiptItem, User
from app.core.security import hasher


def create(client, name="水果", aliases=None, key=None):
    return client.post(
        "/api/v1/products/catalog",
        headers={"Idempotency-Key": key or str(uuid.uuid4())},
        json={"name": name, "aliases": aliases or ["Fresh Fruit", "鮮果"]},
    )


def update(client, product, **changes):
    return client.put(
        "/api/v1/products/catalog/" + product["id"],
        json={
            "name": product["name"],
            "aliases": product["aliases"],
            "note": product["note"],
            "expected_revision": product["revision"],
            **changes,
        },
    )


def test_explicit_links_alias_search_and_unlink_preserve_journal(logged_in, monkeypatch):
    row, bridge = ready(logged_in, monkeypatch)
    product = create(logged_in).json()
    state = item_state(logged_in, row)
    state["items"][0].update(product_id=product["id"], unit="顆", unit_price=None)
    assert save(logged_in, row, state=state).status_code == 200
    assert search(logged_in, "鮮果")["items"] == []  # Pending reviews never count as purchases.
    posted = action(logged_in, row).json()
    before = balances(logged_in)
    for q in ("水果", "鮮果", "fresh fruit", "ＦＲＥＳＨ　 ＦＲＵＩＴ"):
        items = search(logged_in, q)["items"]
        assert len(items) == 1
        assert items[0]["raw_name"] == PARSED["items"][0]["raw_name"]
        assert items[0]["unit_price"] is None and items[0]["unit"] == "顆"
        assert items[0]["product_name"] == "水果"
    assert search(logged_in, "fresh")["items"] == []  # Aliases match the whole normalized name.
    # A literal match plus several aliases cannot duplicate the same purchase.
    renamed = update(
        logged_in, product, name="蘋果", aliases=["Apples", "ＡＰＰＬＥＳ", "鮮果"]
    ).json()
    assert len(search(logged_in, "Apples")["items"]) == 1
    assert search(logged_in, "水果")["items"] == []
    assert search(logged_in, "鮮果")["items"][0]["product_name"] == "蘋果"
    assert tg(logged_in, bridge, 100, text="/lookup 鮮果").status_code == 200
    reply = sent("tg:12345:100")["text"]
    assert "蘋果 Apples" in reply and "2 顆" in reply
    state = item_state(logged_in, posted)
    state["items"][0]["product_id"] = None
    assert save(logged_in, posted, state=state).status_code == 200
    assert search(logged_in, "鮮果")["items"] == []
    assert len(search(logged_in, "蘋果")["items"]) == 1
    assert balances(logged_in) == before
    with Session(engine()) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ReceiptItem)
                .where(ReceiptItem.product_id == uuid.UUID(product["id"]))
            )
            == 2
        )
    assert renamed["revision"] == 2


def test_alias_collision_retry_and_stale_edits_are_atomic(logged_in):
    setup(logged_in)
    key = str(uuid.uuid4())
    p = create(logged_in, key=key)
    assert p.status_code == 200
    assert create(logged_in, key=key).json()["id"] == p.json()["id"]
    assert create(logged_in, name="其他水果", key=key).status_code == 409
    assert create(logged_in, name="ＦＲＥＳＨ  ＦＲＵＩＴ", aliases=["other"]).status_code == 409
    assert len(logged_in.get("/api/v1/products/catalog").json()) == 1
    other = create(logged_in, "麵包", ["Bread"]).json()
    assert update(logged_in, other, aliases=["鮮果"]).status_code == 409
    assert update(logged_in, p.json(), aliases=["Produce"]).status_code == 200
    assert update(logged_in, p.json(), aliases=["Outdated"]).status_code == 409
    all_products = logged_in.get("/api/v1/products/catalog").json()
    assert next(i for i in all_products if i["id"] == other["id"])["aliases"] == ["Bread"]
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Product)) == 2
        assert db.scalar(select(func.count()).select_from(ProductAlias)) == 4


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "  "},
        {"name": "a", "aliases": ["  "]},
        {"name": "a", "aliases": ["a" * 201]},
        {"name": "a", "aliases": ["a"] * 31},
        {"name": "㍿" * 60},  # NFKC expansion exceeds the bounded index key.
    ],
)
def test_alias_validation(logged_in, payload):
    setup(logged_in)
    assert (
        logged_in.post(
            "/api/v1/products/catalog", headers={"Idempotency-Key": str(uuid.uuid4())}, json=payload
        ).status_code
        == 422
    )


def test_owner_isolation_and_csrf_for_catalog_and_item_links(logged_in, monkeypatch):
    row, _ = ready(logged_in, monkeypatch)
    p = create(logged_in).json()
    assert (
        logged_in.post(
            "/api/v1/products/catalog", json={"name": "拒絕"}, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    with transaction() as db:
        user = User(login_name="bob", password_hash=hasher.hash("synthetic-second-passphrase"))
        db.add(user)
        db.flush()
        db.add(BookSettings(owner_id=user.id))
        foreign = Product(owner_id=user.id, name="不可連結", note="", revision=1)
        db.add(foreign)
        db.flush()
        foreign_id = str(foreign.id)
    state = item_state(logged_in, row)
    state["items"][0]["product_id"] = foreign_id
    assert save(logged_in, row, state=state).status_code == 404
    assert item_state(logged_in, row)["revision"] == 0
    assert logged_in.post("/api/v1/auth/logout").status_code == 200
    logged_in.headers["X-CSRF-Token"] = logged_in.get("/api/v1/auth/csrf").json()["token"]
    assert (
        logged_in.post(
            "/api/v1/auth/login",
            json={"username": "bob", "password": "synthetic-second-passphrase"},
        ).status_code
        == 200
    )
    assert (
        logged_in.put(
            "/api/v1/settings",
            json={
                "book_currency": "USD",
                "timezone": "America/Los_Angeles",
                "expected_revision": 0,
            },
        ).status_code
        == 200
    )
    assert [r["id"] for r in logged_in.get("/api/v1/products/catalog").json()] == [foreign_id]
    assert update(logged_in, p, name="侵入").status_code == 404
    assert create(logged_in).status_code == 200  # Alias uniqueness is per owner.
    assert search(logged_in, "鮮果")["items"] == []


def test_previous_schema_backup_restores_and_upgrade_preserves_review(
    logged_in, monkeypatch, tmp_path
):
    from pathlib import Path

    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from alembic import command
    from app.core import backup
    from app.core.config import settings

    row, _ = ready(logged_in, monkeypatch)
    assert save(logged_in, row).status_code == 200
    action(logged_in, row)
    before = search(logged_in)["items"]
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    # conftest enforces finance_test*. Never downgrade an actual ledger.
    command.downgrade(cfg, "0005_items")
    try:
        original_fingerprint = backup.attachment_fingerprint
        with monkeypatch.context() as legacy:
            legacy.setattr(backup, "SCHEMA_VERSION", "0005_items")
            legacy.setattr(
                backup,
                "BACKUP_TABLES",
                tuple(t for t in backup.BACKUP_TABLES if t not in {"products", "product_aliases"}),
            )
            legacy.setattr(
                backup,
                "attachment_fingerprint",
                lambda conn, data_dir: original_fingerprint(conn, data_dir, items_only=True),
            )
            path = backup.create_backup()
    finally:
        command.upgrade(cfg, "head")
    assert search(logged_in)["items"] == before
    url = make_url(settings().database_url.get_secret_value())
    target = "finance_restore_" + uuid.uuid4().hex
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    restored = create_engine(url.set(database=target))
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{target}"'))
    try:
        backup.restore_backup(
            path,
            url.set(database=target).render_as_string(hide_password=False),
            tmp_path / "restored",
        )
        with restored.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0005_items"
            assert conn.scalar(text("SELECT count(*) FROM receipt_items")) == 4
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns WHERE table_name='receipt_items' AND column_name='product_id'"
                    )
                )
                == 0
            )
    finally:
        restored.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{target}" WITH (FORCE)'))
        admin.dispose()
