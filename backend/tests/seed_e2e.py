"""Destructive synthetic browser fixture. Explicitly refuses any production database."""

import os

from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from app.capture.charges import review
from app.capture.currency import decide
from app.capture.schemas import ChargeEvidence, ParsedReceipt, Proposal
from app.cli import initialize_user
from app.core.db import engine, transaction
from app.core.models import Base, CaptureDraft, Receipt, User

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
with transaction() as db:
    owner = db.scalar(select(User.id).where(User.login_name == "alice"))
    for viewport in ("desktop", "mobile"):
        merchant = f"合成幣別測試 {viewport}"
        address = "123 Example St, Palo Alto CA 94301"
        parsed = ParsedReceipt(
            merchant=merchant,
            occurred_on="2026-02-03",
            currency=None,
            amount="10.50",
            subtotal="10.00",
            tax="0.50",
            tip="0",
            discount="0",
            items=[],
            uncertainty=None,
            currency_text="$10.50",
            merchant_address=address,
        )
        source = address + "\nTax 5% $0.50\nSuggested Tip 20% $2.00\nAmount Paid $10.50"
        parsed.charge_evidence = ChargeEvidence.model_validate(
            {
                "tax_lines": [{"text": "Tax 5% $0.50", "amount": "0.50"}],
                "tax_mode": "added",
                "tip": {"text": "Suggested Tip 20% $2.00", "amount": "2.00"},
                "tip_status": "suggested_only",
                "service_charge": None,
                "total": {"text": "Amount Paid $10.50", "amount": "10.50"},
                "total_status": "final",
            }
        )
        charges = review(parsed, source, has_image=False)
        decision = decide(parsed, address, has_image=False)
        receipt = Receipt(owner_id=owner, parse_status="parsed")
        db.add(receipt)
        db.flush()
        db.add(
            CaptureDraft(
                owner_id=owner,
                receipt_id=receipt.id,
                source="web",
                source_key=f"synthetic-currency-{viewport}",
                request_hash="0" * 64,
                source_text=source,
                proposal=Proposal(
                    merchant=merchant,
                    amount="10.50",
                    currency=decision.currency,
                    occurred_on="2026-02-03",
                ).model_dump(mode="json"),
                parsed=parsed.model_dump(mode="json"),
                warnings=[decision.warning, *charges.warnings],
            )
        )
print("Synthetic browser fixture ready.")
