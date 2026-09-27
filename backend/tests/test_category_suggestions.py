import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_capture import PARSED, action, auth, complete, configure, edit, get_draft, image, next_job
from test_ledger import balances, expense, post, setup

from app.core.db import engine, transaction
from app.core.models import (
    BookSettings,
    Category,
    JournalEntry,
    ReceiptParseAttempt,
    Transaction,
    User,
    now,
)
from app.core.security import hasher


def request(c, draft, **overrides):
    body = dict(expected_revision=draft["revision"], kind="expense", merchant="", note="")
    return c.post(
        f"/api/v1/capture/drafts/{draft['id']}/category-suggestions", json={**body, **overrides}
    )


def suggest(c, draft, **overrides):
    response = request(c, draft, **overrides)
    assert response.status_code == 200, response.text
    return response.json()


def counts():
    with Session(engine()) as db:
        return [
            db.scalar(select(func.count()).select_from(m))
            for m in (Category, Transaction, JournalEntry, ReceiptParseAttempt)
        ]


def test_membership_proposes_a_new_category_without_writing_or_external_calls(logged_in):
    setup(logged_in)
    draft = image(logged_in)
    before = counts(), balances(logged_in), get_draft(logged_in, draft)
    result = suggest(logged_in, draft, merchant="Synthetic Garden", note="Membership 會員費用")
    assert result["suggestions"][0] == dict(
        category_id=None,
        name="會員／訂閱",
        source="receipt_text",
        reason="商家、備註或品項出現「membership」；尚無對應分類，可建立後選用。",
    )
    assert result["draft_revision"] == draft["revision"]
    assert (counts(), balances(logged_in), get_draft(logged_in, draft)) == before
    assert request(logged_in, draft, note="x" * 2001).status_code == 422


def test_existing_child_category_kind_and_latin_word_boundaries(logged_in):
    setup(logged_in)
    parent = post(logged_in, "/categories", dict(name="休閒", kind="expense"))
    member = post(
        logged_in, "/categories", dict(name="會員費", kind="expense", parent_id=parent["id"])
    )
    draft = image(logged_in)
    choices = suggest(logged_in, draft, note="ＭＥＭＢＥＲＳＨＩＰ")["suggestions"]
    assert choices[0]["category_id"] == member["id"]
    assert choices[0]["name"] == "休閒 → 會員費"
    assert not any(
        s["source"] == "receipt_text"
        for s in suggest(logged_in, draft, note="address membershipless")["suggestions"]
    )
    income = suggest(logged_in, draft, kind="income", note="salary membership")
    assert income["suggestions"][0]["name"] == "薪資"
    assert all(s["category_id"] != member["id"] for s in income["suggestions"])
    with transaction() as db:
        db.get(Category, uuid.UUID(member["id"])).archived_at = now()
    archived = suggest(logged_in, draft, note="membership")["suggestions"]
    assert all(s["category_id"] != member["id"] and s["name"] != "會員／訂閱" for s in archived)


def test_history_uses_current_posted_revisions_not_voids_or_other_merchants(logged_in):
    _, card, _, food, fee, _ = setup(logged_in)
    one = post(logged_in, "/transactions", expense(card, fee, merchant="SYNTHETIC  CLUB"))
    two = post(logged_in, "/transactions", expense(card, fee, merchant="synthetic club"))
    post(logged_in, "/transactions", expense(card, food, merchant="synthetic club"))
    for _ in range(3):
        post(logged_in, "/transactions", expense(card, food, merchant="different shop"))
    draft = image(logged_in)
    choices = suggest(
        logged_in, draft, merchant="Ｓｙｎｔｈｅｔｉｃ   Ｃｌｕｂ", note="membership"
    )["suggestions"]
    assert choices[0]["category_id"] == fee["id"] and "2 筆" in choices[0]["reason"]
    assert choices[1]["category_id"] == food["id"] and "1 筆" in choices[1]["reason"]
    assert choices[2]["name"] == "會員／訂閱"
    corrected = logged_in.put(
        "/api/v1/transactions/" + one["id"],
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={
            **expense(card, food, merchant="synthetic club"),
            "expected_revision": 1,
            "reason": "合成分類更正",
        },
    )
    assert corrected.status_code == 200, corrected.text
    post(
        logged_in,
        "/transactions/" + two["id"] + "/void",
        dict(expected_revision=1, reason="合成作廢"),
    )
    history = [
        s
        for s in suggest(logged_in, draft, merchant="synthetic club")["suggestions"]
        if s["source"] == "merchant_history"
    ]
    assert (
        len(history) == 1
        and history[0]["category_id"] == food["id"]
        and "2 筆" in history[0]["reason"]
    )


def test_splits_count_once_per_transaction_and_never_suggest_amounts(logged_in):
    _, card, _, food, fee, _ = setup(logged_in)
    post(
        logged_in,
        "/transactions",
        dict(
            kind="expense",
            occurred_on="2026-01-05",
            account_id=card["id"],
            amount="10",
            merchant="Synthetic",
            splits=[
                dict(category_id=food["id"], amount="3"),
                dict(category_id=food["id"], amount="2"),
                dict(category_id=fee["id"], amount="5"),
            ],
        ),
    )
    result = suggest(logged_in, image(logged_in), merchant="Synthetic")
    assert len(result["suggestions"]) == 2
    assert all(
        s["source"] == "merchant_history" and "1 筆" in s["reason"] and "amount" not in s
        for s in result["suggestions"]
    )


def test_unknown_and_common_are_explicitly_not_evidence(logged_in):
    assert (
        logged_in.put(
            "/api/v1/settings",
            json=dict(book_currency="USD", timezone="America/Los_Angeles", expected_revision=0),
        ).status_code
        == 200
    )
    draft = image(logged_in)
    result = suggest(logged_in, draft)
    assert "資訊不足" in result["message"]
    assert result["suggestions"][0]["category_id"] is None
    assert result["suggestions"][0]["source"] == "fallback"
    card = post(
        logged_in,
        "/accounts",
        dict(name="合成現金", kind="cash", currency="USD", opening_on="2026-01-01"),
    )
    food = post(logged_in, "/categories", dict(name="食材", kind="expense"))
    post(logged_in, "/transactions", expense(card, food, merchant="another merchant"))
    result = suggest(logged_in, draft, merchant="unseen merchant")
    assert "資訊不足" in result["message"]
    assert result["suggestions"][0]["source"] == "common"
    assert "尚無證據" in result["suggestions"][0]["reason"]


def test_ocr_items_and_current_reviews_then_revision_fence(logged_in, monkeypatch):
    configure(monkeypatch)
    setup(logged_in)
    draft = image(logged_in)
    headers = auth()
    job = next_job(logged_in, headers)
    parsed = {
        **PARSED,
        "merchant": "Synthetic",
        "items": [dict(raw_name="Membership", quantity=None, unit_price=None, line_total="10.50")],
    }
    assert complete(logged_in, headers, job, parsed=parsed).status_code == 200
    draft = get_draft(logged_in, draft)
    assert suggest(logged_in, draft)["suggestions"][0]["name"] == "會員／訂閱"
    draft = edit(logged_in, draft, currency="USD")
    review = logged_in.get(f"/api/v1/capture/drafts/{draft['id']}/items").json()
    response = logged_in.put(
        f"/api/v1/capture/drafts/{draft['id']}/items",
        json=dict(
            expected_revision=review["revision"],
            expected_draft_revision=draft["revision"],
            expected_transaction_revision=None,
            acknowledged=True,
            items=[dict(name="牛奶", quantity=None, unit_price=None, line_total=None)],
        ),
    )
    assert response.status_code == 200, response.text
    choices = suggest(logged_in, draft)["suggestions"]
    assert choices[0]["name"] == "食材" and all(s["name"] != "會員／訂閱" for s in choices)
    response = logged_in.put(
        f"/api/v1/capture/drafts/{draft['id']}/items",
        json=dict(
            expected_revision=response.json()["revision"],
            expected_draft_revision=draft["revision"],
            expected_transaction_revision=None,
            acknowledged=True,
            items=[],
        ),
    )
    assert response.status_code == 200
    assert all(s["source"] != "receipt_text" for s in suggest(logged_in, draft)["suggestions"])
    changed = edit(logged_in, draft, merchant="Updated")
    assert request(logged_in, draft).status_code == 409
    assert suggest(logged_in, changed)["suggestions"][0]["name"] == "會員／訂閱"
    assert action(logged_in, changed, "cancel").status_code == 200
    assert request(logged_in, get_draft(logged_in, changed)).status_code == 409


def test_owner_csrf_and_auth_isolation(logged_in):
    setup(logged_in)
    draft = image(logged_in)
    path = f"/api/v1/capture/drafts/{draft['id']}/category-suggestions"
    body = dict(expected_revision=draft["revision"], kind="expense", merchant="", note="membership")
    assert logged_in.post(path, json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert (
        logged_in.post(path, json=body, headers={"Origin": "https://wrong.invalid"}).status_code
        == 403
    )
    with transaction() as db:
        user = User(login_name="bob", password_hash=hasher.hash("another-synthetic-passphrase"))
        db.add(user)
        db.flush()
        db.add(BookSettings(owner_id=user.id))
    logged_in.post("/api/v1/auth/logout")
    logged_in.headers["X-CSRF-Token"] = logged_in.get("/api/v1/auth/csrf").json()["token"]
    assert logged_in.post(path, json=body).status_code == 401
    assert (
        logged_in.post(
            "/api/v1/auth/login",
            json={"username": "bob", "password": "another-synthetic-passphrase"},
        ).status_code
        == 200
    )
    assert logged_in.post(path, json=body).status_code == 404
    assert (
        logged_in.put(
            "/api/v1/settings",
            json=dict(book_currency="USD", timezone="America/Los_Angeles", expected_revision=0),
        ).status_code
        == 200
    )
    own = suggest(logged_in, image(logged_in), note="牛奶")
    assert all(s["category_id"] is None for s in own["suggestions"])
