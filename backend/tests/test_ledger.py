import io
import json
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.db import transaction
from app.core.models import JournalEntry, Posting, Transaction, User
from app.ledger.schemas import TransactionInput
from app.ledger.service import create_transaction


def post(client, path, body, key=None):
    response = client.post(
        "/api/v1" + path, json=body, headers={"Idempotency-Key": key or str(uuid.uuid4())}
    )
    assert response.status_code == 200, response.text
    return response.json()


def setup(client):
    response = client.put(
        "/api/v1/settings",
        json={"book_currency": "USD", "timezone": "America/Los_Angeles", "expected_revision": 0},
    )
    assert response.status_code == 200
    bank = post(
        client,
        "/accounts",
        dict(
            name="銀行",
            kind="bank",
            currency="USD",
            opening_balance="1000",
            opening_on="2026-01-01",
        ),
    )
    card = post(
        client,
        "/accounts",
        dict(name="信用卡", kind="credit_card", currency="USD", opening_on="2026-01-01"),
    )
    cash = post(
        client, "/accounts", dict(name="現金", kind="cash", currency="USD", opening_on="2026-01-01")
    )
    food = post(client, "/categories", dict(name="食材", kind="expense"))
    fee = post(client, "/categories", dict(name="手續費", kind="expense"))
    salary = post(client, "/categories", dict(name="薪資", kind="income"))
    return bank, card, cash, food, fee, salary


def report(client, **kwargs):
    return post(client, "/reports", dict(start="2026-01-01", end_exclusive="2026-02-01", **kwargs))


def expense(card, food, **kwargs):
    return dict(
        kind="expense",
        occurred_on="2026-01-05",
        account_id=card["id"],
        amount="100",
        category_id=food["id"],
        **kwargs,
    )


def balances(client):
    return {a["name"]: a["balance"] for a in client.get("/api/v1/accounts").json()}


def test_card_repayment_refund_and_snapshot(logged_in):
    c = logged_in
    bank, card, _, food, _, _ = setup(c)
    first = report(c)["document"]
    assert first["metrics"]["income"]["value"] == "0"
    assert first["metrics"]["net_worth"]["value"] == "1000"
    purchase = post(c, "/transactions", expense(card, food))
    assert balances(c)["信用卡"] == "100"
    post(
        c,
        "/transactions",
        dict(
            kind="transfer",
            occurred_on="2026-01-06",
            account_id=bank["id"],
            to_account_id=card["id"],
            amount="100",
        ),
    )
    frozen = report(c)
    assert frozen["document"]["metrics"]["expense"]["value"] == "100"
    assert frozen["document"]["metrics"]["net_worth"]["value"] == "900"
    refund = post(
        c,
        "/transactions",
        dict(
            kind="refund",
            occurred_on="2026-01-07",
            account_id=card["id"],
            amount="20",
            refund_of_id=purchase["id"],
        ),
    )
    assert balances(c) == {"銀行": "900", "信用卡": "-20", "現金": "0"}
    current = report(c)["document"]
    assert current["metrics"]["expense"]["value"] == "80"
    assert current["metrics"]["net_worth"]["value"] == "920"
    assert current["metrics"]["savings_rate"]["value"] is None
    assert c.get("/api/v1/reports/" + frozen["id"]).json() == frozen
    archive = zipfile.ZipFile(io.BytesIO(c.get("/api/v1/reports/" + frozen["id"] + "/csv").content))
    assert json.loads(archive.read("metadata.json"))["snapshot_id"] == frozen["id"]
    assert "expense,100,complete" in archive.read("summary.csv").decode("utf-8-sig")
    excess = c.post(
        "/api/v1/transactions",
        json=dict(
            kind="refund",
            occurred_on="2026-01-08",
            account_id=card["id"],
            amount="81",
            refund_of_id=purchase["id"],
        ),
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert excess.status_code == 409
    assert (
        c.post(
            "/api/v1/transactions/" + purchase["id"] + "/void",
            json=dict(expected_revision=1, reason="取消"),
            headers={"Idempotency-Key": str(uuid.uuid4())},
        ).status_code
        == 409
    )
    post(c, "/transactions/" + refund["id"] + "/void", dict(expected_revision=1, reason="退款取消"))
    assert report(c)["document"]["metrics"]["expense"]["value"] == "100"


def test_transfer_fee_split_filter_and_precision(logged_in):
    c = logged_in
    bank, _, cash, food, fee, _ = setup(c)
    post(
        c,
        "/transactions",
        dict(
            kind="transfer",
            occurred_on="2026-01-02",
            account_id=bank["id"],
            to_account_id=cash["id"],
            amount="200",
            fee="2",
            fee_category_id=fee["id"],
        ),
    )
    assert balances(c)["銀行"] == "798"
    assert balances(c)["現金"] == "200"
    assert report(c)["document"]["metrics"]["expense"]["value"] == "2"
    split = dict(
        kind="expense",
        occurred_on="2026-01-03",
        account_id=cash["id"],
        amount="100",
        splits=[
            dict(category_id=food["id"], amount="60"),
            dict(category_id=fee["id"], amount="40"),
        ],
    )
    txn = post(c, "/transactions", split)
    filtered = report(c, category_id=food["id"])["document"]
    assert filtered["metrics"]["expense"]["value"] == "60"
    assert filtered["rows"][0]["expense"] == "60"
    assert filtered["metrics"]["net_worth"]["value"] is None
    post(
        c,
        "/transactions",
        dict(
            kind="refund",
            occurred_on="2026-01-05",
            account_id=cash["id"],
            amount="20",
            refund_of_id=txn["id"],
        ),
    )
    assert report(c, category_id=food["id"])["document"]["metrics"]["expense"]["value"] == "48"
    invalid = c.post(
        "/api/v1/transactions",
        json={**expense(cash, food), "amount": "0.001"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert invalid.status_code == 422


def test_revisions_reversal_and_idempotency(logged_in):
    c = logged_in
    bank, _, _, food, _, _ = setup(c)
    body = expense(bank, food, note='=SUM(1,2)\n引號"中文', tags=["出差"])
    key = str(uuid.uuid4())
    txn = post(c, "/transactions", body, key)
    assert report(c, query="出差")["document"]["metrics"]["expense"]["value"] == "100"
    assert c.get("/api/v1/transactions?query=出差").json()["total"] == 1
    assert c.get("/api/v1/transactions?query=不存在").json()["total"] == 0
    assert post(c, "/transactions", body, key)["id"] == txn["id"]
    assert (
        c.post(
            "/api/v1/transactions", json={**body, "amount": "99"}, headers={"Idempotency-Key": key}
        ).status_code
        == 409
    )
    old = report(c)
    revised = {
        **body,
        "amount": "80",
        "occurred_on": "2026-02-02",
        "expected_revision": 1,
        "reason": "日期與金額更正",
    }
    response = c.put(
        "/api/v1/transactions/" + txn["id"],
        json=revised,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 200, response.text
    assert balances(c)["銀行"] == "920"
    assert report(c)["document"]["metrics"]["expense"]["value"] == "0"
    assert (
        c.put(
            "/api/v1/transactions/" + txn["id"],
            json=revised,
            headers={"Idempotency-Key": str(uuid.uuid4())},
        ).status_code
        == 409
    )
    archive = zipfile.ZipFile(io.BytesIO(c.get("/api/v1/reports/" + old["id"] + "/csv").content))
    assert "'=SUM(1,2)" in archive.read("transactions.csv").decode("utf-8-sig")
    post(c, "/transactions/" + txn["id"] + "/void", dict(expected_revision=2, reason="取消"))
    assert balances(c)["銀行"] == "1000"
    with transaction() as db:
        assert (
            db.scalar(
                select(text("count(*)"))
                .select_from(JournalEntry)
                .where(JournalEntry.transaction_id == uuid.UUID(txn["id"]))
            )
            == 4
        )
        assert db.scalar(select(text("sum(book_amount_signed)")).select_from(Posting)) == 0


def test_fx_missing_quote_then_manual_valuation(logged_in):
    c = logged_in
    bank, _, _, _, _, _ = setup(c)
    twd = post(
        c, "/accounts", dict(name="台幣", kind="bank", currency="TWD", opening_on="2026-01-01")
    )
    post(
        c,
        "/transactions",
        dict(
            kind="transfer",
            occurred_on="2026-01-02",
            account_id=bank["id"],
            to_account_id=twd["id"],
            amount="100",
            received_amount="3200",
            received_fx_rate="0.03125",
        ),
    )
    assert balances(c)["台幣"] == "3200"
    assert report(c)["document"]["metrics"]["net_worth"]["value"] is None
    post(
        c,
        "/fx-quotes",
        dict(currency="TWD", effective_on="2026-01-30", rate="0.03125", source="虛構測試匯率"),
    )
    doc = report(c)["document"]
    assert doc["metrics"]["net_worth"]["value"] == "1000"
    assert doc["metrics"]["expense"]["value"] == "0"


def test_db_guards_and_concurrent_submission(logged_in):
    c = logged_in
    bank, _, _, food, _, _ = setup(c)
    body = TransactionInput(**expense(bank, food))
    with transaction() as db:
        owner = db.scalar(select(User.id))

    def submit():
        with transaction() as db:
            return create_transaction(db, owner, body, "same-concurrent-key")

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: submit(), range(2)))
    assert a == b
    assert balances(c)["銀行"] == "900"
    for sql in (
        "UPDATE postings SET amount_signed=1",
        "DELETE FROM journal_entries",
        "UPDATE report_snapshots SET document='{}'::jsonb",
    ):
        report(c)
        with pytest.raises(IntegrityError), transaction() as db:
            db.execute(text(sql))
    with pytest.raises(IntegrityError), transaction() as db:
        db.execute(
            text(
                "INSERT INTO postings SELECT entry_id,line_no+10,ledger_account_id,currency,amount_signed,book_amount_signed,fx_rate,component,owner_id,gen_random_uuid(),created_at,updated_at FROM postings LIMIT 1"
            )
        )
    with pytest.raises(IntegrityError), transaction() as db:
        txn = Transaction(owner_id=owner, kind="expense")
        db.add(txn)
        db.flush()
        journal = JournalEntry(
            owner_id=owner,
            transaction_id=txn.id,
            revision_no=1,
            entry_role="original",
            occurred_on=__import__("datetime").date(2026, 1, 1),
            book_currency="USD",
        )
        db.add(journal)
        db.flush()
        txn.current_entry_id = journal.id


def test_auth_owner_and_currency_lock(logged_in):
    c = logged_in
    bank, _, _, food, _, _ = setup(c)
    safe = dict(c.headers)
    c.headers.pop("Origin")
    c.headers.pop("X-CSRF-Token")
    assert c.get("/api/v1/accounts").status_code == 200
    assert c.get("/api/v1/categories").status_code == 200
    c.headers.update(safe)
    assert (
        c.put(
            "/api/v1/settings",
            json=dict(book_currency="TWD", timezone="Asia/Taipei", expected_revision=1),
        ).status_code
        == 409
    )
    assert (
        c.post(
            "/api/v1/transactions",
            json={**expense(bank, food), "account_id": str(uuid.uuid4())},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        ).status_code
        == 404
    )
    assert c.get("/api/v1/reports/" + str(uuid.uuid4()) + "/csv").status_code == 404
    assert (
        c.post(
            "/api/v1/transactions",
            json=expense(bank, food),
            headers={"Idempotency-Key": str(uuid.uuid4()), "X-CSRF-Token": "bad"},
        ).status_code
        == 403
    )


def test_refunds_and_updates_compete_without_double_write(logged_in):
    c = logged_in
    bank, card, _, food, _, _ = setup(c)
    purchase = post(c, "/transactions", expense(card, food))

    def refund(_):
        return c.post(
            "/api/v1/transactions",
            json=dict(
                kind="refund",
                occurred_on="2026-01-08",
                account_id=card["id"],
                amount="60",
                refund_of_id=purchase["id"],
            ),
            headers={"Idempotency-Key": str(uuid.uuid4())},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(refund, range(2))) == [200, 409]
    assert report(c)["document"]["metrics"]["expense"]["value"] == "40"
    another = post(c, "/transactions", expense(bank, food))

    def update(amount):
        return c.put(
            "/api/v1/transactions/" + another["id"],
            json={
                **expense(bank, food),
                "amount": amount,
                "expected_revision": 1,
                "reason": "競爭測試",
            },
            headers={"Idempotency-Key": str(uuid.uuid4())},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, ["70", "80"])) == [200, 409]
    assert balances(c)["銀行"] in ("920", "930")
