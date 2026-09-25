import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_capture import PARSED, auth, complete, configure, edit, get_draft, next_job, tg
from test_ledger import balances, setup
from test_receipts import picture

from app.core.db import engine, transaction
from app.core.models import Job, ReceiptParseAttempt, now


def sent(key):
    with Session(engine()) as db:
        return db.scalar(
            select(Job.payload).where(Job.kind == "telegram_send", Job.logical_key == key)
        )


def buttons(message):
    return [
        button
        for row in message.get("reply_markup", {}).get("inline_keyboard", [])
        for button in row
    ]


def click(client, headers, event, message, label, **kwargs):
    data = next(button["callback_data"] for button in buttons(message) if button["text"] == label)
    assert len(data.encode()) <= 64
    tg(client, headers, event, callback=data, **kwargs)
    return data


def parsed_draft(client, headers):
    tg(client, headers, 1, text="合成收據文字")
    row = client.get("/api/v1/capture/drafts").json()[0]
    job = next_job(client, headers)
    assert complete(client, headers, job, parsed=PARSED).status_code == 200
    return get_draft(client, row), job, sent(f"parsed:{job['id']}")


def test_currency_buttons_preserve_raw_parse_and_require_matching_account(logged_in, monkeypatch):
    configure(monkeypatch)
    _, card, _, food, _, _ = setup(logged_in)
    headers = auth()
    before = balances(logged_in)
    row, _, message = parsed_draft(logged_in, headers)
    assert "小計 10.00" in message["text"] and "小費 0" in message["text"]
    assert "AI 建議幣別：USD" in message["text"]
    assert row["proposal"]["currency"] is None
    click(logged_in, headers, 2, message, "選擇幣別")
    menu = sent("tg:12345:2")
    assert "不會換算金額" in menu["text"]
    assert get_draft(logged_in, row)["revision"] == row["revision"]
    data = click(logged_in, headers, 3, menu, "TWD · 新台幣")
    tg(logged_in, headers, 3, callback=data)  # Redelivered update is ignored.
    click(logged_in, headers, 4, menu, "USD · 美元")  # Old revision cannot overwrite it.
    current = get_draft(logged_in, row)
    assert current["revision"] == row["revision"] + 1
    assert current["proposal"]["currency"] == "TWD"
    assert current["proposal"]["amount"] == "10.50"
    assert current["parsed"] == row["parsed"]
    assert "草稿已更新" in sent("tg:12345:4:error")["text"]
    ident = uuid.UUID(row["id"]).hex
    tg(
        logged_in,
        headers,
        5,
        callback=f"acct:{ident}:{current['revision']}:{uuid.UUID(card['id']).hex[:8]}",
    )
    current = get_draft(logged_in, row)
    tg(
        logged_in,
        headers,
        6,
        callback=f"cat:{ident}:{current['revision']}:{uuid.UUID(food['id']).hex[:8]}",
    )
    current = get_draft(logged_in, row)
    tg(logged_in, headers, 7, callback=f"confirm:{ident}:{current['revision']}")
    assert "幣別必須與記帳帳戶相同" in sent("tg:12345:7:error")["text"]
    assert get_draft(logged_in, row)["status"] == "needs_review"
    assert balances(logged_in) == before


def test_retry_confirmation_duplicate_callbacks_and_history(logged_in, monkeypatch):
    configure(monkeypatch)
    setup(logged_in)
    headers = auth()
    before = balances(logged_in)
    row, first_job, _ = parsed_draft(logged_in, headers)
    row = edit(logged_in, row, amount="77", merchant="人工核對")
    ident = uuid.UUID(row["id"]).hex
    tg(logged_in, headers, 2, callback=f"view:{ident}:1")
    click(logged_in, headers, 3, sent("tg:12345:2"), "重新辨識")
    confirmation = sent("tg:12345:3")
    assert "可能取代已儲存的手動修正" in confirmation["text"]
    assert next_job(logged_in, headers) is None
    assert get_draft(logged_in, row)["proposal"]["amount"] == "77"
    data = click(logged_in, headers, 4, confirmation, "確定重新辨識")
    tg(logged_in, headers, 4, callback=data)
    tg(logged_in, headers, 5, callback=data)
    running = get_draft(logged_in, row)
    assert running["status"] == "processing" and running["revision"] == row["revision"] + 1
    assert running["proposal"]["amount"] == "77"
    message = sent("tg:12345:4")
    assert {b["text"] for b in buttons(message)} == {"查看進度", "取消草稿"}
    assert "前次辨識明細" in message["text"]
    tg(logged_in, headers, 6, callback=f"retryok:{ident}:{running['revision']}")
    assert "仍在處理" in sent("tg:12345:6:error")["text"]
    job = next_job(logged_in, headers)
    assert job["id"] != first_job["id"] and next_job(logged_in, headers) is None
    assert (
        complete(logged_in, headers, job, parsed={**PARSED, "subtotal": "9.00"}).status_code == 200
    )
    result = get_draft(logged_in, row)
    assert result["status"] == "needs_review" and result["confirmed_transaction_id"] is None
    with Session(engine()) as db:
        assert (
            db.scalar(select(func.count()).select_from(Job).where(Job.kind == "capture_parse")) == 2
        )
        attempts = list(
            db.scalars(select(ReceiptParseAttempt).order_by(ReceiptParseAttempt.created_at))
        )
        assert [a.result["subtotal"] for a in attempts] == ["10.00", "9.00"]
    assert balances(logged_in) == before


def test_new_buttons_reject_untrusted_closed_and_disabled_actions(logged_in, monkeypatch):
    configure(monkeypatch, "disabled")
    setup(logged_in)
    headers = auth()
    before = balances(logged_in)
    tg(logged_in, headers, 1, text="合成草稿")
    row = logged_in.get("/api/v1/capture/drafts").json()[0]
    message = sent("tg:12345:1")
    assert "重新辨識" not in {b["text"] for b in buttons(message)}
    ident = uuid.UUID(row["id"]).hex
    tg(logged_in, headers, 2, callback=f"retryok:{ident}:{row['revision']}")
    assert "尚未啟用辨識" in sent("tg:12345:2:error")["text"]
    for event, value in [(3, "EUR"), (4, ""), (5, "USD:extra")]:
        tg(logged_in, headers, event, callback=f"curr:{ident}:{row['revision']}:{value}")
    assert get_draft(logged_in, row)["revision"] == row["revision"]
    assert (
        tg(
            logged_in, headers, 6, callback=f"curr:{ident}:{row['revision']}:USD", user_id=999
        ).json()["accepted"]
        is False
    )
    assert get_draft(logged_in, row)["proposal"]["currency"] is None
    click(logged_in, headers, 7, message, "取消草稿")
    closed = get_draft(logged_in, row)
    assert not buttons(sent("tg:12345:7"))
    tg(logged_in, headers, 8, callback=f"curr:{ident}:{closed['revision']}:USD")
    assert "已完成或取消" in sent("tg:12345:8:error")["text"]
    assert get_draft(logged_in, row)["proposal"]["currency"] is None
    assert next_job(logged_in, headers) is None and balances(logged_in) == before


def test_failed_photo_download_can_retry_without_ai_or_losing_image(logged_in, monkeypatch):
    configure(monkeypatch, "disabled")
    setup(logged_in)
    headers = auth()
    before = balances(logged_in)
    tg(logged_in, headers, 1, file_id="synthetic-photo", filename="test.png")
    row = logged_in.get("/api/v1/capture/drafts").json()[0]
    for _ in range(5):
        job = next_job(logged_in, headers, "telegram_download")
        assert complete(logged_in, headers, job, error_code="remote_unavailable").status_code == 200
        with transaction() as db:
            db.get(Job, uuid.UUID(job["id"])).run_after = now() - timedelta(seconds=1)
    row = get_draft(logged_in, row)
    assert row["status"] == "failed" and row["attachment_id"] is None
    message = sent(f"download-failed:{job['id']}")
    click(logged_in, headers, 2, message, "重試下載")
    click(logged_in, headers, 3, sent("tg:12345:2"), "確定重試下載")
    retried = next_job(logged_in, headers, "telegram_download")
    assert retried["id"] != job["id"]
    uploaded = logged_in.post(
        f"/api/v1/capture-bridge/jobs/{retried['id']}/image",
        headers={**headers, "X-Job-Lease": retried["lease_token"]},
        content=picture(),
    )
    assert uploaded.status_code == 200
    result = get_draft(logged_in, row)
    assert result["attachment_id"] and result["status"] == "needs_review"
    assert next_job(logged_in, headers) is None and balances(logged_in) == before


def test_review_card_preserves_unknowns_and_stale_retry_cannot_replace_edits(
    logged_in, monkeypatch
):
    configure(monkeypatch)
    setup(logged_in)
    headers = auth()
    tg(logged_in, headers, 1, text="合成測試")
    row = logged_in.get("/api/v1/capture/drafts").json()[0]
    job = next_job(logged_in, headers)
    complete(
        logged_in, headers, job, parsed={**PARSED, "subtotal": None, "tip": "0", "discount": None}
    )
    message = sent(f"parsed:{job['id']}")
    assert "小計 不明" in message["text"] and "折扣 不明" in message["text"]
    assert "小費 0" in message["text"]
    click(logged_in, headers, 2, message, "重新辨識")
    row = edit(logged_in, get_draft(logged_in, row), amount="88")
    click(logged_in, headers, 3, sent("tg:12345:2"), "確定重新辨識")
    assert "草稿已更新" in sent("tg:12345:3:error")["text"]
    assert get_draft(logged_in, row)["proposal"]["amount"] == "88"
    assert next_job(logged_in, headers) is None
