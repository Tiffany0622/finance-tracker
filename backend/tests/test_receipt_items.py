import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_capture import (
    PARSED,
    REQUEST,
    action,
    auth,
    complete,
    configure,
    edit,
    get_draft,
    image,
    next_job,
    tg,
)
from test_ledger import balances, setup
from test_telegram_review import sent

from app.capture.items import ReviewInput, save_review
from app.core.auth import ApiError
from app.core.db import engine, transaction
from app.core.models import BookSettings, CaptureDraft, ReceiptItemReview, ReceiptParseAttempt, User
from app.core.security import hasher


def item_state(client, row):
    response = client.get(f"/api/v1/capture/drafts/{row['id']}/items")
    assert response.status_code == 200, response.text
    return response.json()


def review_body(state, items=None):
    return dict(
        expected_revision=state["revision"],
        expected_draft_revision=state["draft_revision"],
        expected_transaction_revision=state["transaction_revision"],
        acknowledged=True,
        items=[
            {k: v for k, v in item.items() if k != "raw_name"}
            for item in (state["items"] if items is None else items)
        ],
    )


def save(client, row, items=None, state=None):
    return client.put(
        f"/api/v1/capture/drafts/{row['id']}/items",
        json=review_body(state or item_state(client, row), items),
    )


def search(client, query="", offset=0):
    r = client.get("/api/v1/products/history", params={"q": query, "offset": offset})
    assert r.status_code == 200, r.text
    return r.json()


def ready(client, monkeypatch):
    configure(monkeypatch)
    _, card, _, food, _, _ = setup(client)
    headers = auth()
    row = image(client)
    assert complete(client, headers, next_job(client, headers), parsed=PARSED).status_code == 200
    row = edit(
        client,
        get_draft(client, row),
        currency="USD",
        account_id=card["id"],
        category_id=food["id"],
    )
    return row, headers


def test_review_confirm_search_correct_and_remove_preserve_raw_and_ledger(logged_in, monkeypatch):
    row, _ = ready(logged_in, monkeypatch)
    before = balances(logged_in)
    state = item_state(logged_in, row)
    assert state["status"] == "unreviewed" and state["revision"] == 0
    state["items"][0].update(
        name="富士蘋果",
        quantity="2",
        unit_price="1.75",
        line_total="3.5",
        unit="顆",
        note="會員折扣",
    )
    r = save(logged_in, row, state=state)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "reviewed" and r.json()["warnings"]
    assert search(logged_in)["items"] == [] and balances(logged_in) == before
    posted = action(logged_in, row).json()
    assert posted["status"] == "confirmed"
    assert action(logged_in, row).status_code == 200
    assert balances(logged_in)["信用卡"] == "10.5"
    found = search(logged_in, "蘋果")["items"]
    assert len(found) == 1 and found[0]["unit_price"] == "1.75"
    assert found[0]["line_total"] == "3.5" and found[0]["raw_name"] == "蘋果 Apples"
    assert (
        found[0]["merchant"] == PARSED["merchant"]
        and found[0]["occurred_on"] == PARSED["occurred_on"]
    )
    assert search(logged_in, "APPLES")["items"] == found
    assert search(logged_in)["pending_receipts"] == 0
    assert get_draft(logged_in, row)["parsed"] == PARSED
    after = balances(logged_in)
    state = item_state(logged_in, posted)
    state["items"][0]["unit_price"] = None
    assert save(logged_in, posted, state=state).status_code == 200
    assert search(logged_in, "蘋果")["items"][0]["unit_price"] is None
    assert save(logged_in, posted, []).status_code == 200
    assert search(logged_in)["items"] == [] and balances(logged_in) == after
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(ReceiptItemReview)) == 4
        assert db.scalar(select(ReceiptParseAttempt.result)) == PARSED
    with pytest.raises(DBAPIError), transaction() as db:
        db.execute(text("UPDATE receipt_items SET name='overwritten'"))


def test_old_posted_receipt_unknowns_literal_search_pagination_and_cancel(logged_in, monkeypatch):
    row, _ = ready(logged_in, monkeypatch)
    row = action(logged_in, row).json()
    assert search(logged_in)["pending_receipts"] == 1
    assert item_state(logged_in, row)["status"] == "unreviewed"
    lines = [
        dict(
            name=f"50%_OFF 商品 {i}",
            quantity=None,
            unit_price=None,
            line_total="0" if i == 0 else None,
        )
        for i in range(27)
    ]
    assert save(logged_in, row, lines).status_code == 200
    first = search(logged_in, "%_")
    second = search(logged_in, "%_", 25)
    assert (
        len(first["items"]) == 25
        and first["has_more"]
        and len(second["items"]) == 2
        and not second["has_more"]
    )
    assert first["items"][0]["line_total"] == "0" and first["items"][0]["unit_price"] is None
    assert not search(logged_in, "50%X")["items"]
    cancelled = image(logged_in)
    cancelled = action(logged_in, cancelled, "cancel").json()
    assert save(logged_in, cancelled, []).status_code == 409


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", "0"),
        ("quantity", "-2"),
        ("unit_price", "-1"),
        ("unit_price", "NaN"),
        ("quantity", "1e2"),
        ("name", "  "),
    ],
)
def test_invalid_items_rejected(logged_in, monkeypatch, field, value):
    row, _ = ready(logged_in, monkeypatch)
    assert save(logged_in, row, [{"name": "合成商品", field: value}]).status_code == 422
    assert item_state(logged_in, row)["revision"] == 0


def test_stale_edits_retry_and_simultaneous_review(logged_in, monkeypatch):
    row, headers = ready(logged_in, monkeypatch)
    state = item_state(logged_in, row)
    body = ReviewInput.model_validate(review_body(state))
    with Session(engine()) as db:
        owner = db.scalar(select(User.id).where(User.login_name == "alice"))

    def run():
        try:
            with transaction() as db:
                save_review(db, owner, uuid.UUID(row["id"]), body, REQUEST)
            return 200
        except ApiError as e:
            return e.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: run(), range(2))) == [200, 409]
    retried = action(logged_in, row, "retry").json()
    assert save(logged_in, retried).status_code == 409
    updated = {
        **PARSED,
        "items": [
            {"raw_name": "新的辨識字串", "quantity": "1", "unit_price": "10", "line_total": "10"}
        ],
    }
    assert (
        complete(logged_in, headers, next_job(logged_in, headers), parsed=updated).status_code
        == 200
    )
    row = get_draft(logged_in, row)
    state = item_state(logged_in, row)
    assert state["status"] == "stale" and len(state["items"]) == 2
    row = edit(logged_in, row, currency="USD")
    assert save(logged_in, row, state=state).status_code == 409
    assert save(logged_in, row).status_code == 200
    with Session(engine()) as db:
        newest = db.scalar(select(ReceiptItemReview).order_by(ReceiptItemReview.revision.desc()))
        assert newest.parsed_snapshot == PARSED
        assert db.scalar(select(func.count()).select_from(ReceiptParseAttempt)) == 2


def test_transaction_revision_requires_review_and_void_hides_history(logged_in, monkeypatch):
    row, _ = ready(logged_in, monkeypatch)
    assert save(logged_in, row).status_code == 200
    row = action(logged_in, row).json()
    state = item_state(logged_in, row)
    proposal = {k: v for k, v in row["proposal"].items() if k != "currency"}
    r = logged_in.put(
        f"/api/v1/transactions/{row['confirmed_transaction_id']}",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={**proposal, "expected_revision": 1, "reason": "合成測試", "merchant": "修正商家"},
    )
    assert r.status_code == 200, r.text
    assert search(logged_in)["items"] == [] and search(logged_in)["pending_receipts"] == 1
    assert save(logged_in, row, state=state).status_code == 409
    assert item_state(logged_in, row)["status"] == "stale"
    assert save(logged_in, row).status_code == 200
    assert search(logged_in)["items"][0]["merchant"] == "修正商家"
    r = logged_in.post(
        f"/api/v1/transactions/{row['confirmed_transaction_id']}/void",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"expected_revision": 2, "reason": "合成作廢"},
    )
    assert r.status_code == 200, r.text
    assert not search(logged_in)["items"] and not item_state(logged_in, row)["editable"]
    assert save(logged_in, row).status_code == 409


def test_owner_csrf_and_telegram_lookup_is_read_only(logged_in, monkeypatch):
    row, headers = ready(logged_in, monkeypatch)
    assert save(logged_in, row).status_code == 200
    row = action(logged_in, row).json()
    before = balances(logged_in)
    tg(logged_in, headers, 1, text="/lookup 蘋果")
    assert "單價 2" in sent("tg:12345:1")["text"]
    assert "USD" in sent("tg:12345:1")["text"]
    tg(logged_in, headers, 2, text="/lookup 蘋果", file_id="query-only-photo")
    assert "不會建立記帳草稿" in sent("tg:12345:2")["text"]
    tg(logged_in, headers, 3, text="/lookup 蘋果", user_id=999)
    assert sent("tg:12345:3") is None
    assert balances(logged_in) == before
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CaptureDraft)) == 1
    assert (
        logged_in.put(
            f"/api/v1/capture/drafts/{row['id']}/items",
            headers={"X-CSRF-Token": "invalid"},
            json=review_body(item_state(logged_in, row)),
        ).status_code
        == 403
    )
    with transaction() as db:
        user = User(login_name="bob", password_hash=hasher.hash("synthetic-second-passphrase"))
        db.add(user)
        db.flush()
        db.add(BookSettings(owner_id=user.id))
    assert logged_in.post("/api/v1/auth/logout").status_code == 200
    logged_in.headers["X-CSRF-Token"] = logged_in.get("/api/v1/auth/csrf").json()["token"]
    assert (
        logged_in.post(
            "/api/v1/auth/login",
            json={"username": "bob", "password": "synthetic-second-passphrase"},
        ).status_code
        == 200
    )
    assert logged_in.get(f"/api/v1/capture/drafts/{row['id']}/items").status_code == 404
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
    assert (
        logged_in.put(
            f"/api/v1/capture/drafts/{row['id']}/items",
            json=review_body(
                {"revision": 0, "draft_revision": 1, "transaction_revision": None, "items": []}
            ),
        ).status_code
        == 404
    )
    assert search(logged_in)["items"] == [] and search(logged_in)["pending_receipts"] == 0
