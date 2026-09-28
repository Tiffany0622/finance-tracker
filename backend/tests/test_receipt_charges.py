"""Synthetic payment summaries: independently expected money, never live receipts."""

from decimal import Decimal

import pytest
from sqlalchemy import select
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
from test_telegram_review import sent

from app.capture import providers, service
from app.capture.charges import review
from app.capture.schemas import ParsedReceipt
from app.core.db import engine
from app.core.models import ReceiptParseAttempt


def line(text, amount):
    return {"text": text, "amount": amount}


def receipt(**changes):
    evidence = dict(
        tax_lines=[line("Tax 8% $4.00", "4.00")],
        tax_mode="added",
        tip=line("Tip $10.00", "10.00"),
        tip_status="paid",
        service_charge=None,
        total=line("Amount Paid $64.00", "64.00"),
        total_status="final",
    )
    evidence.update(changes)
    return ParsedReceipt.model_validate(
        {
            **PARSED,
            "amount": "64",
            "subtotal": "50",
            "tax": "4",
            "tip": "10",
            "items": [],
            "currency_text": "Amount Paid US$64.00",
            "charge_evidence": evidence,
        }
    )


def reviewed(**changes):
    result = review(receipt(**changes), "", has_image=True)
    assert result is not None
    return result


@pytest.mark.parametrize(
    "text,amount,expected",
    [
        ("Tax 8.25% $4.13", "4.13", "4.13"),
        ("Tax 8.25%", "8.25", None),
        ("Tax 8.25%", "8", None),
        ("Tax 8.25%", "25", None),
        ("Tax 8 %", "8", None),
        ("Tax $1,234.56", "1234.56", "1234.56"),
        ("稅額 ＄０．００", "0", "0"),
        ("Tax $4.00", "8", None),
    ],
)
def test_tax_amount_must_be_money_not_rate(text, amount, expected):
    actual = reviewed(tax_lines=[line(text, amount)]).tax
    assert (Decimal(actual) if actual is not None else None) == (
        Decimal(expected) if expected is not None else None
    )


def test_multiple_taxes_summary_duplicates_and_conflicts():
    parts = [line("GST 5% $2.50", "2.50"), line("PST 7% $3.50", "3.50")]
    assert Decimal(reviewed(tax_lines=parts).tax) == Decimal("6")
    summary = line("Total Tax $6.00", "6.00")
    assert Decimal(reviewed(tax_lines=[*parts, summary, summary]).tax) == Decimal("6")
    assert reviewed(tax_lines=[*parts, line("Total Tax $7.00", "7")]).tax is None
    assert reviewed(tax_lines=[*parts, line("HST 8%", None)]).tax is None


@pytest.mark.parametrize(
    "status,text,amount,expected",
    [
        ("suggested_only", "Suggested Tip 20% $10.00", "10", None),
        ("paid", "Suggested Tip 20% $10.00", "10", None),
        ("blank", "Tip __________", None, None),
        ("not_printed", "", None, None),
        ("paid", "Tip $0.00", "0", "0"),
        ("paid", "Gratuity $10.00", "10", "10"),
        ("paid", "Tip 20%", "20", None),
    ],
)
def test_actual_tip_only(status, text, amount, expected):
    assert reviewed(tip_status=status, tip=line(text, amount)).tip == expected


@pytest.mark.parametrize(
    "status,total,expected",
    [
        ("before_tip", "Total $54.00", None),
        ("final", "Total $54.00", None),
        ("final", "Amount Paid $54.00", "54"),
        ("final", "Cash Tendered $54.00", None),
        ("final", "Change $54.00", None),
        ("final", "Subtotal $54.00", None),
        ("final", "Pre-tip Total $54.00", None),
        ("final", "Authorization Total $54.00", None),
    ],
)
def test_blank_tip_and_non_final_payment(status, total, expected):
    assert (
        reviewed(
            tip_status="blank",
            tip=line("Tip ___", None),
            total_status=status,
            total=line(total, "54"),
        ).amount
        == expected
    )


def test_included_tax_and_service_fee_are_not_added_twice():
    parsed = receipt(
        tax_mode="included",
        tax_lines=[line("VAT included $2", "2")],
        service_charge=line("Service Fee $3", "3"),
        total=line("Amount Paid $63", "63"),
    )
    charges = review(parsed, "", has_image=True)
    assert charges and charges.tax == "2" and charges.service_charge == "3"
    warnings = service.validate_parsed(parsed, charges=charges)
    assert any("含稅" in w for w in warnings)
    assert not any("不符" in w for w in warnings)  # 50 inclusive + 10 tip + 3 fee = 63.
    parsed.charge_evidence.total.amount = "65"
    parsed.charge_evidence.total.text = "Amount Paid $65"
    assert any(
        "不符" in w
        for w in service.validate_parsed(parsed, charges=review(parsed, "", has_image=True))
    )
    duplicate = line("Service Charge / Gratuity $10", "10")
    result = reviewed(tip=duplicate, service_charge=duplicate)
    assert result.tip is None and result.service_charge == "10"


def test_text_evidence_cannot_invent_a_paid_tip_or_tax():
    result = review(receipt(), "Subtotal $50 Tax 8% Total $54 Tip ___", has_image=False)
    assert result and result.tax is None and result.tip is None and result.amount is None
    assert review(ParsedReceipt.model_validate(PARSED), "", has_image=True) is None


def test_new_schema_required_but_old_provider_schemas_unchanged(monkeypatch):
    calls = []

    def fake(url, payload, headers=None):
        if payload and "messages" in payload:
            calls.append(payload)
        return {"message": {"content": receipt().model_dump_json()}}

    monkeypatch.setattr(providers, "request_json", fake)
    for version in (1, 2, 3, 4):
        providers.parse(
            "ollama",
            "synthetic",
            "",
            b"synthetic",
            ollama_url="http://localhost:11434",
            openai_key="",
            prompt_version=version,
        )
    for old in calls[:3]:
        assert "charge_evidence" not in old["format"]["properties"]
        assert "ChargeEvidence" not in old["format"].get("$defs", {})
    assert "charge_evidence" in calls[3]["format"]["required"]
    assert "Suggested/recommended" in calls[3]["messages"][0]["content"]


def test_telegram_charge_review_preserves_raw_and_only_posts_confirmed_amount(
    logged_in, monkeypatch
):
    configure(monkeypatch)
    _, card, _, food, _, _ = setup(logged_in)
    before = balances(logged_in)
    headers = auth()
    tg(logged_in, headers, 1, file_id="synthetic-tax-tip")
    download = next_job(logged_in, headers, "telegram_download")
    assert (
        logged_in.post(
            f"/api/v1/capture-bridge/jobs/{download['id']}/image",
            headers={**headers, "X-Job-Lease": download["lease_token"]},
            content=picture(),
        ).status_code
        == 200
    )
    row = logged_in.get("/api/v1/capture/drafts").json()[0]
    job = next_job(logged_in, headers)
    raw = receipt(
        tip_status="suggested_only",
        tip=line("Suggested Tip 20% $10", "10"),
        total=line("Amount Paid $54", "54"),
    ).model_dump(mode="json")
    # Deliberately wrong raw model total/tip are retained, but not used as the reviewed total/tip.
    assert raw["amount"] == "64" and raw["tip"] == "10"
    assert complete(logged_in, headers, job, parsed=raw).status_code == 200
    assert complete(logged_in, headers, job, parsed=raw).json()["code"] == "lease_lost"
    result = get_draft(logged_in, row)
    assert result["proposal"]["amount"] == "54" and result["charge_review"]["tip"] is None
    assert result["parsed"] == raw and balances(logged_in) == before
    message = sent(f"parsed:{job['id']}")["text"]
    assert "小費 不明（僅建議小費）" in message and "稅 4.00" in message
    with Session(engine()) as db:
        attempts = db.scalars(select(ReceiptParseAttempt)).all()
        assert len(attempts) == 1 and attempts[0].result == raw
        assert attempts[0].prompt_version == 4 and attempts[0].schema_version == 3
    fixed = edit(logged_in, result, amount="59", account_id=card["id"], category_id=food["id"])
    assert fixed["charge_review"]["amount"] == "54" and fixed["parsed"] == raw
    confirmed = action(logged_in, fixed)
    assert confirmed.status_code == 200
    transaction = next(
        t
        for t in logged_in.get("/api/v1/transactions").json()["items"]
        if t["id"] == confirmed.json()["confirmed_transaction_id"]
    )
    assert Decimal(transaction["amount"]) == Decimal("59")  # Never adds tax/tip on top.


def test_stale_charge_parse_does_not_overwrite_manual_edit(logged_in, monkeypatch):
    configure(monkeypatch)
    setup(logged_in)
    headers = auth()
    row = image(logged_in)
    job = next_job(logged_in, headers)
    fixed = edit(logged_in, row, amount="70")
    raw = receipt().model_dump(mode="json")
    assert complete(logged_in, headers, job, parsed=raw).status_code == 200
    current = get_draft(logged_in, row)
    assert current["proposal"]["amount"] == "70" and current["revision"] == fixed["revision"]
    assert current["parsed"] is None and current["charge_review"] is None
    with Session(engine()) as db:
        assert db.scalar(select(ReceiptParseAttempt)).result == raw


def test_charge_limits_and_tiny_decimals_do_not_break_draft_output():
    assert (
        reviewed(
            tax_lines=[
                line("Tax A 99999999999999", "99999999999999"),
                line("Tax B 99999999999999", "99999999999999"),
            ]
        ).tax
        is None
    )
    assert (
        reviewed(tax_lines=[line("Tax 0.000000000000000001", "0.000000000000000001")]).tax
        == "0.000000000000000001"
    )
