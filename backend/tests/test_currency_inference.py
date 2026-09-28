import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_capture import (
    PARSED,
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
from test_receipts import picture
from test_telegram_review import click, sent

from app.capture import providers
from app.capture.currency import decide
from app.capture.schemas import ParsedReceipt
from app.core.db import engine
from app.core.models import ReceiptParseAttempt, Transaction


def parsed(**changes):
    return ParsedReceipt.model_validate({**PARSED, "currency": None, **changes})


@pytest.mark.parametrize(
    "quote,address,expected,basis",
    [
        ("TOTAL US$ 10.50", "", "USD", "explicit"),
        ("實付 ＮＴ＄ １００", "", "TWD", "explicit"),
        ("Total New Taiwan Dollars 100", "", "TWD", "explicit"),
        ("Total USD 10", "100 Test Rd, Taipei, Taiwan", "USD", "explicit"),
        ("Total $10.50", "123 Example St, Palo Alto, CA 94301", "USD", "address"),
        (None, "123 Example St, San Francisco, CA 94105-1234", "USD", "address"),
        (None, "123 Example Road, Boston, United States", "USD", "address"),
        (None, "台北市大安區信義路三段100號", "TWD", "address"),
        (None, "No. 100 Test Rd, Taipei, Taiwan", "TWD", "address"),
        ("$10", "", None, "unknown"),
        ("Total dollars 10", "CA", None, "unknown"),
        (None, "USA", None, "unknown"),
        ("$10", "100 Test St, Vancouver BC V6B 1A1, Canada", None, "unknown"),
        ("$10", "100 Test St, Perth WA 6000, Australia", None, "unknown"),
        ("$10", "123 Example St, Palo Alto CA 94301, Canada", None, "unknown"),
        (None, "123 Example St, Taipei, Taiwan USA", None, "unknown"),
        ("CAD 10.50", "123 Test St, Palo Alto CA 94301", None, "unsupported"),
        ("A$10.50", "123 Test St, Palo Alto CA 94301", None, "unsupported"),
        ("€10.50", "123 Test St, Palo Alto CA 94301", None, "unsupported"),
        ("¥1000", "", None, "unsupported"),
        ("USD 10 / TWD 300", "", None, "conflict"),
        ("USD 10 / CAD 13", "123 Test St, Palo Alto CA 94301", None, "conflict"),
    ],
)
def test_currency_evidence_and_ambiguous_regions(quote, address, expected, basis):
    decision = decide(parsed(currency_text=quote, merchant_address=address), "", has_image=True)
    assert (decision.currency, decision.basis) == (expected, basis)
    if basis == "address":
        assert "地區推測" in decision.warning and address in decision.warning


def test_text_only_evidence_must_be_in_original_and_language_is_not_country():
    invented = parsed(
        currency="USD", currency_text="US$10", merchant_address="123 Test St, Palo Alto CA 94301"
    )
    for source in ("English receipt Total $10", "中文收據 總額 $10", ""):
        assert decide(invented, source, has_image=False).currency is None
    assert decide(invented, "Paid USD 10", has_image=False).currency == "USD"
    assert decide(invented, "Paid NT$300", has_image=False).currency == "TWD"
    address = "123 Test St, Palo Alto CA 94301"
    assert decide(parsed(merchant_address=address), address, has_image=False).currency == "USD"
    assert decide(parsed(currency_text="TWD 100"), "USD 3", has_image=True).basis == "conflict"
    assert (
        decide(parsed(currency="CAD", merchant_address=address), "", has_image=True).basis
        == "unsupported"
    )


@pytest.mark.parametrize(
    "details,expected",
    [
        ({"currency": "USD", "currency_text": "US$10.50"}, "USD"),
        (
            {
                "currency": None,
                "currency_text": "$10.50",
                "merchant_address": "123 Test St, Palo Alto CA 94301",
            },
            "USD",
        ),
        ({"currency": None, "merchant_address": "臺北市大安區信義路100號"}, "TWD"),
        ({"currency": "USD", "currency_text": "$10.50"}, None),
        (
            {
                "currency": "CAD",
                "currency_text": "CAD 10.50",
                "merchant_address": "123 Test St, Palo Alto CA 94301",
            },
            None,
        ),
        ({"currency": "USD", "currency_text": "USD 10.50 / TWD 330"}, None),
    ],
)
def test_image_parse_applies_reviewable_default_without_posting(
    logged_in, monkeypatch, details, expected
):
    configure(monkeypatch)
    _, card, _, food, _, _ = setup(logged_in)
    before = balances(logged_in)
    with Session(engine()) as db:
        count_before = db.scalar(select(func.count()).select_from(Transaction))
    headers = auth()
    row = image(logged_in)
    job = next_job(logged_in, headers)
    source = {**PARSED, **details}
    assert job["payload"]["prompt_version"] == 4 and job["payload"]["schema_version"] == 3
    assert complete(logged_in, headers, job, parsed=source).status_code == 200
    result = get_draft(logged_in, row)
    assert result["proposal"]["currency"] == expected
    assert result["proposal"]["amount"] == "10.50"
    assert result["status"] == "needs_review" and result["confirmed_transaction_id"] is None
    assert balances(logged_in) == before and result["warnings"][0].startswith("幣別判斷：")
    with Session(engine()) as db:
        attempt = db.scalar(select(ReceiptParseAttempt))
        assert (
            attempt.result == source and attempt.prompt_version == 4 and attempt.schema_version == 3
        )
        assert db.scalar(select(func.count()).select_from(Transaction)) == count_before
    # Manual currency changes retain amount and evidence. Acknowledgement remains required.
    fixed = edit(logged_in, result, currency="USD", account_id=card["id"], category_id=food["id"])
    assert fixed["parsed"] == result["parsed"] and fixed["proposal"]["amount"] == "10.50"
    assert action(logged_in, fixed, acknowledge=False).json()["code"] == "draft_warnings"
    confirmed = action(logged_in, fixed)
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"
    assert action(logged_in, fixed).status_code == 200
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == count_before + 1


def test_telegram_photo_prefills_currency_explains_and_keeps_manual_override(
    logged_in, monkeypatch
):
    configure(monkeypatch)
    setup(logged_in)
    headers = auth()
    before = balances(logged_in)
    tg(logged_in, headers, 1, file_id="synthetic-us-address")
    download = next_job(logged_in, headers, "telegram_download")
    response = logged_in.post(
        f"/api/v1/capture-bridge/jobs/{download['id']}/image",
        headers={**headers, "X-Job-Lease": download["lease_token"]},
        content=picture(),
    )
    assert response.status_code == 200
    row = logged_in.get("/api/v1/capture/drafts").json()[0]
    job = next_job(logged_in, headers)
    assert (
        complete(
            logged_in,
            headers,
            job,
            parsed={
                **PARSED,
                "currency": None,
                "currency_text": "$10.50",
                "merchant_address": "123 Test St, Palo Alto CA 94301",
            },
        ).status_code
        == 200
    )
    current = get_draft(logged_in, row)
    message = sent(f"parsed:{job['id']}")
    assert "金額：USD 10.50" in message["text"] and "地區推測" in message["text"]
    assert "Palo Alto CA 94301" in message["text"]
    click(logged_in, headers, 2, message, "選擇幣別")
    menu = sent("tg:12345:2")
    callback = click(logged_in, headers, 3, menu, "TWD · 新台幣")
    tg(logged_in, headers, 3, callback=callback)
    click(logged_in, headers, 4, menu, "USD · 美元")
    changed = get_draft(logged_in, row)
    assert changed["proposal"]["currency"] == "TWD" and changed["proposal"]["amount"] == "10.50"
    assert changed["revision"] == current["revision"] + 1 and changed["parsed"] == current["parsed"]
    assert balances(logged_in) == before


def test_provider_requires_evidence_fields_for_v3_but_keeps_v2_schema(monkeypatch):
    calls = []

    def fake(url, payload, headers=None):
        calls.append(payload)
        return {"message": {"content": json.dumps(PARSED)}}

    monkeypatch.setattr(providers, "request_json", fake)
    for version in (2, 3):
        providers.parse(
            "ollama",
            "synthetic",
            "",
            b"synthetic",
            ollama_url="http://localhost:11434",
            openai_key="",
            prompt_version=version,
        )
    old, new = [call["format"] for call in calls]
    assert "merchant_address" not in old["properties"]
    assert {"merchant_address", "currency_text"}.issubset(new["required"])
    assert "never infer country" in calls[1]["messages"][0]["content"]
