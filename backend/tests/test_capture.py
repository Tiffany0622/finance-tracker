import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_ledger import balances, setup
from test_receipts import picture

from app.capture import providers, service
from app.capture.bridge import Bridge, normalize
from app.capture.prompts import PROMPT_V1, PROMPT_VERSION
from app.capture.schemas import DraftAction, ParsedReceipt
from app.capture.transport import RemoteError
from app.core.backup import create_backup, restore_backup, verify_backup
from app.core.config import settings
from app.core.db import engine, transaction
from app.core.jobs import claim
from app.core.models import (
    ApiToken,
    CaptureDraft,
    Job,
    ReceiptParseAttempt,
    TelegramEvent,
    Transaction,
    User,
    now,
)
from app.core.security import digest, hasher

PARSED = dict(
    merchant="合成超市 Synthetic Market",
    occurred_on="2026-01-05",
    currency="USD",
    amount="10.50",
    subtotal="10.00",
    tax="0.50",
    tip="0",
    discount="0",
    items=[
        dict(raw_name="蘋果 Apples", quantity="2", unit_price="2", line_total="4"),
        dict(raw_name="Bread", quantity="1", unit_price="6", line_total="6"),
    ],
    uncertainty=None,
)
REQUEST = SimpleNamespace(state=SimpleNamespace(request_id="synthetic-capture-test"))


def configure(monkeypatch, provider="ollama"):
    monkeypatch.setenv("CAPTURE_PROVIDER", provider)
    monkeypatch.setenv("CAPTURE_MODEL", "synthetic-vision")
    monkeypatch.setenv("TELEGRAM_BOT_ID", "12345")
    monkeypatch.setenv("TELEGRAM_USER_ID", "67890")
    settings.cache_clear()


def auth():
    token = "synthetic-bridge-token-" + uuid.uuid4().hex
    with transaction() as db:
        owner = db.scalar(select(User.id).where(User.login_name == "alice"))
        db.add(
            ApiToken(
                owner_id=owner,
                token_hash=digest(token),
                scopes=["capture:bridge"],
                expires_at=now() + timedelta(days=1),
            )
        )
    return {"Authorization": "Bearer " + token}


def image(client, key=None, data=None):
    result = client.post(
        "/api/v1/capture/image?filename=synthetic.png",
        content=picture() if data is None else data,
        headers={"Content-Type": "image/png", "Idempotency-Key": key or str(uuid.uuid4())},
    )
    assert result.status_code == 200, result.text
    return result.json()


def get_draft(client, draft):
    return client.get("/api/v1/capture/drafts/" + draft["id"]).json()


def edit(client, draft, **changes):
    result = client.put(
        "/api/v1/capture/drafts/" + draft["id"],
        json={"expected_revision": draft["revision"], "proposal": {**draft["proposal"], **changes}},
    )
    assert result.status_code == 200, result.text
    return result.json()


def action(client, draft, name="confirm", acknowledge=True):
    return client.post(
        f"/api/v1/capture/drafts/{draft['id']}/{name}",
        json={"expected_revision": draft["revision"], "acknowledge_warnings": acknowledge},
    )


def next_job(client, headers, kind="capture_parse"):
    result = client.post("/api/v1/capture-bridge/claim", json={"kinds": [kind]}, headers=headers)
    assert result.status_code == 200, result.text
    return result.json()


def complete(client, headers, job, **body):
    return client.post(
        f"/api/v1/capture-bridge/jobs/{job['id']}/result",
        json={"lease_token": job["lease_token"], **body},
        headers=headers,
    )


def tg(client, headers, update_id, **body):
    result = client.post(
        "/api/v1/capture-bridge/updates",
        json={
            "update_id": update_id,
            "bot_id": 12345,
            "user_id": 67890,
            "chat_id": 67890,
            "private": True,
            **body,
        },
        headers=headers,
    )

    assert result.status_code == 200, result.text
    return result


def test_disabled_image_manual_confirm_once_and_receipt_link(logged_in):
    _, card, _, food, _, _ = setup(logged_in)
    before = balances(logged_in)
    draft = image(logged_in, "capture-retry-key")
    assert draft["status"] == "needs_review" and draft["parsed"] is None
    assert image(logged_in, "capture-retry-key")["id"] == draft["id"]
    assert balances(logged_in) == before
    draft = edit(
        logged_in,
        draft,
        amount="10.50",
        occurred_on="2026-01-05",
        currency="USD",
        account_id=card["id"],
        category_id=food["id"],
    )
    assert action(logged_in, draft, acknowledge=False).status_code == 422
    first = action(logged_in, draft)
    assert first.status_code == 200, first.text
    again = action(logged_in, draft)
    assert again.json()["confirmed_transaction_id"] == first.json()["confirmed_transaction_id"]
    assert balances(logged_in)["信用卡"] == "10.5"
    attachments = logged_in.get(
        "/api/v1/transactions/" + first.json()["confirmed_transaction_id"] + "/attachments"
    ).json()
    assert [row["id"] for row in attachments] == [draft["attachment_id"]]
    assert action(logged_in, first.json(), "cancel").status_code == 409


def test_ocr_validated_but_no_account_invented_or_ledger_write(logged_in, monkeypatch):
    configure(monkeypatch)
    setup(logged_in)
    headers = auth()
    before = balances(logged_in)
    draft = image(logged_in)
    assert claim() is None  # Core worker cannot accidentally consume network jobs.
    job = next_job(logged_in, headers)
    content = logged_in.get(
        f"/api/v1/capture-bridge/jobs/{job['id']}/image",
        headers={**headers, "X-Job-Lease": job["lease_token"]},
    )
    assert content.status_code == 200 and content.headers["content-type"] == "image/jpeg"
    assert complete(logged_in, headers, job, parsed=PARSED).status_code == 200
    result = get_draft(logged_in, draft)
    assert result["status"] == "needs_review" and result["proposal"]["amount"] == "10.50"
    assert result["proposal"]["account_id"] is None and result["proposal"]["category_id"] is None
    assert result["parsed"]["items"][0]["raw_name"] == "蘋果 Apples"
    assert result["proposal"]["currency"] is None
    assert result["parsed"]["currency"] == "USD"
    assert result["warnings"] == ["AI 幣別僅供參考，請對照收據手動選擇；不以地址或 $ 符號推定。"]
    assert balances(logged_in) == before
    assert action(logged_in, result).status_code == 422
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(ReceiptParseAttempt)) == 1


def test_late_parse_cannot_overwrite_manual_edit_or_cancel(logged_in, monkeypatch):
    configure(monkeypatch)
    setup(logged_in)
    headers = auth()
    draft = image(logged_in)
    job = next_job(logged_in, headers)
    draft = edit(logged_in, draft, amount="77", merchant="人工核對")
    assert complete(logged_in, headers, job, parsed=PARSED).status_code == 200
    assert get_draft(logged_in, draft)["proposal"]["amount"] == "77"
    retried = action(logged_in, draft, "retry").json()
    job = next_job(logged_in, headers)
    assert action(logged_in, retried, "cancel").status_code == 200
    assert complete(logged_in, headers, job, parsed=PARSED).status_code == 200
    assert get_draft(logged_in, draft)["status"] == "cancelled"


def test_stale_worker_fenced_reclaim_and_rate_limit_visible(logged_in, monkeypatch):
    configure(monkeypatch)
    setup(logged_in)
    headers = auth()
    draft = image(logged_in)
    old = next_job(logged_in, headers)
    with transaction() as db:
        db.get(Job, uuid.UUID(old["id"])).lease_until = now() - timedelta(seconds=1)
    fresh = next_job(logged_in, headers)
    assert fresh["id"] == old["id"] and fresh["lease_token"] != old["lease_token"]
    assert complete(logged_in, headers, old, parsed=PARSED).status_code == 409
    assert (
        complete(logged_in, headers, fresh, error_code="rate_limited", retry_after=100).status_code
        == 200
    )
    result = get_draft(logged_in, draft)
    assert result["job_status"] == "retry_wait" and result["error_code"] == "rate_limited"
    with Session(engine()) as db:
        assert db.get(Job, uuid.UUID(old["id"])).run_after > now() + timedelta(seconds=90)


def test_retry_exhaustion_and_new_manual_retry(logged_in, monkeypatch):
    configure(monkeypatch)
    setup(logged_in)
    headers = auth()
    draft = image(logged_in)
    for _ in range(5):
        job = next_job(logged_in, headers)
        assert complete(logged_in, headers, job, error_code="remote_unavailable").status_code == 200
        with transaction() as db:
            db.get(Job, uuid.UUID(job["id"])).run_after = now() - timedelta(seconds=1)
    draft = get_draft(logged_in, draft)
    assert draft["status"] == "failed"
    retry = action(logged_in, draft, "retry")
    assert retry.status_code == 200 and retry.json()["status"] == "processing"
    assert next_job(logged_in, headers)["id"] != job["id"]


def test_unknowns_and_bad_totals_are_warnings_not_zero():
    unknown = ParsedReceipt.model_validate(
        {**PARSED, "amount": None, "currency": None, "tax": None}
    )
    assert unknown.amount is None
    assert any("總額" in v for v in service.validate_parsed(unknown))
    invalid = ParsedReceipt.model_validate({**PARSED, "amount": "99"})
    assert any("總額不符" in v for v in service.validate_parsed(invalid))
    bad_items = ParsedReceipt.model_validate(
        {
            **PARSED,
            "items": [{"raw_name": "合成", "quantity": "3", "unit_price": "2", "line_total": "4"}],
        }
    )
    assert len(service.validate_parsed(bad_items)) == 2
    with pytest.raises(ValueError):
        ParsedReceipt.model_validate({**PARSED, "amount": "NaN"})
    with pytest.raises(ValueError):
        ParsedReceipt.model_validate({**PARSED, "occurred_on": "2026-02-30"})


def test_splits_and_currency_checks_reuse_ledger(logged_in):
    _, card, _, food, fee, _ = setup(logged_in)
    draft = image(logged_in)
    draft = edit(
        logged_in,
        draft,
        currency="TWD",
        amount="10",
        occurred_on="2026-01-05",
        account_id=card["id"],
        splits=[
            {"category_id": food["id"], "amount": "4"},
            {"category_id": fee["id"], "amount": "5"},
        ],
    )
    assert action(logged_in, draft).json()["code"] == "currency_mismatch"
    draft = edit(logged_in, draft, currency="USD")
    assert action(logged_in, draft).status_code == 422
    draft = edit(
        logged_in,
        draft,
        splits=[
            {"category_id": food["id"], "amount": "4"},
            {"category_id": fee["id"], "amount": "6"},
        ],
    )
    assert action(logged_in, draft).status_code == 200
    assert balances(logged_in)["信用卡"] == "10"


def test_competing_confirms_create_one_ledger_transaction(logged_in):
    _, card, _, food, _, _ = setup(logged_in)
    draft = edit(
        logged_in,
        image(logged_in),
        amount="12",
        currency="USD",
        occurred_on="2026-01-05",
        account_id=card["id"],
        category_id=food["id"],
    )
    with Session(engine()) as db:
        owner = db.scalar(select(User.id).where(User.login_name == "alice"))

    def confirm(_):
        with transaction() as db:
            row = service.confirm(
                db,
                owner,
                uuid.UUID(draft["id"]),
                DraftAction(expected_revision=draft["revision"], acknowledge_warnings=True),
                REQUEST,
            )
            return row.confirmed_transaction_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, range(2)))
    assert results[0] == results[1] and balances(logged_in)["信用卡"] == "12"


def test_draft_owner_csrf_service_scope_and_revocation(logged_in, monkeypatch):
    configure(monkeypatch)
    setup(logged_in)
    draft = image(logged_in)
    assert (
        logged_in.put(
            "/api/v1/capture/drafts/" + draft["id"],
            json={"expected_revision": 1, "proposal": draft["proposal"]},
            headers={"X-CSRF-Token": "invalid"},
        ).status_code
        == 403
    )
    assert logged_in.get("/api/v1/capture-bridge/cursor").status_code == 401
    headers = auth()
    with transaction() as db:
        token = db.scalar(select(ApiToken))
        token.scopes = ["reports:read"]
    assert logged_in.get("/api/v1/capture-bridge/cursor", headers=headers).status_code == 401
    with transaction() as db:
        token = db.scalar(select(ApiToken))
        token.scopes = ["capture:bridge"]
        token.revoked_at = now()
    assert logged_in.get("/api/v1/capture-bridge/cursor", headers=headers).status_code == 401
    with transaction() as db:
        db.add(User(login_name="bob", password_hash=hasher.hash("another-synthetic-passphrase")))
    logged_in.post("/api/v1/auth/logout")
    csrf = logged_in.get("/api/v1/auth/csrf").json()["token"]
    logged_in.headers["X-CSRF-Token"] = csrf
    assert (
        logged_in.post(
            "/api/v1/auth/login",
            json={"username": "bob", "password": "another-synthetic-passphrase"},
        ).status_code
        == 200
    )
    assert logged_in.get("/api/v1/capture/drafts/" + draft["id"]).status_code == 404
    assert (
        logged_in.get("/api/v1/attachments/" + draft["attachment_id"] + "/preview").status_code
        == 404
    )


def test_telegram_whitelist_duplicate_event_cursor_and_download(logged_in, monkeypatch):
    configure(monkeypatch, "disabled")
    setup(logged_in)
    headers = auth()
    assert (
        tg(logged_in, headers, 1, user_id=5, file_id="private-fake-file").json()["accepted"]
        is False
    )
    assert (
        tg(logged_in, headers, 2, private=False, file_id="group-file").json()["accepted"] is False
    )
    assert logged_in.get("/api/v1/capture/drafts").json() == []
    update = tg(logged_in, headers, 3, file_id="synthetic-file", filename="receipt.png")
    assert update.json() == {"next_offset": 4, "accepted": True}
    assert (
        tg(logged_in, headers, 3, file_id="synthetic-file", filename="receipt.png").json()
        == update.json()
    )
    assert (
        logged_in.get("/api/v1/capture-bridge/cursor", headers=headers).json()["next_offset"] == 4
    )
    drafts = logged_in.get("/api/v1/capture/drafts").json()
    assert len(drafts) == 1 and drafts[0]["status"] == "processing"
    job = next_job(logged_in, headers, "telegram_download")
    result = logged_in.post(
        f"/api/v1/capture-bridge/jobs/{job['id']}/image",
        content=picture(),
        headers={
            **headers,
            "Content-Type": "application/octet-stream",
            "X-Job-Lease": job["lease_token"],
        },
    )
    assert result.status_code == 200, result.text
    result = get_draft(logged_in, drafts[0])
    assert result["attachment_id"] and result["status"] == "needs_review"
    with Session(engine()) as db:
        events = list(db.scalars(select(TelegramEvent)))
        assert len(events) == 3
        assert db.scalar(select(func.count()).select_from(CaptureDraft)) == 1


def test_telegram_edit_account_category_old_buttons_and_duplicate_confirm(logged_in, monkeypatch):
    configure(monkeypatch, "disabled")
    _, card, _, food, _, _ = setup(logged_in)
    headers = auth()
    tg(logged_in, headers, 1, text="合成咖啡 USD 10")
    row = logged_in.get("/api/v1/capture/drafts").json()[0]
    ident = uuid.UUID(row["id"]).hex
    tg(
        logged_in,
        headers,
        2,
        text=f'/edit {ident[:8]} amount=10 date=2026-01-05 currency=USD merchant="合成咖啡"',
    )
    row = get_draft(logged_in, row)
    tg(
        logged_in,
        headers,
        3,
        callback=f"acct:{ident}:{row['revision']}:{uuid.UUID(card['id']).hex[:8]}",
    )
    row = get_draft(logged_in, row)
    tg(
        logged_in,
        headers,
        4,
        callback=f"cat:{ident}:{row['revision']}:{uuid.UUID(food['id']).hex[:8]}",
    )
    row = get_draft(logged_in, row)
    tg(logged_in, headers, 5, callback=f"confirm:{ident}:1")
    assert get_draft(logged_in, row)["status"] == "needs_review"
    tg(logged_in, headers, 6, callback=f"confirm:{ident}:{row['revision']}")
    tg(logged_in, headers, 7, callback=f"confirm:{ident}:{row['revision']}")
    assert get_draft(logged_in, row)["status"] == "confirmed", json.dumps(
        get_draft(logged_in, row), ensure_ascii=False
    )
    assert balances(logged_in)["信用卡"] == "10"
    tg(logged_in, headers, 8, text="/pending")
    tg(logged_in, headers, 9, text="/today")
    tg(logged_in, headers, 10, text="/month")
    tg(logged_in, headers, 11, text="/networth")
    tg(logged_in, headers, 12, text="/budget")
    with Session(engine()) as db:
        texts = [
            j.payload["text"] for j in db.scalars(select(Job).where(Job.kind == "telegram_send"))
        ]
        assert any("草稿已更新" in t for t in texts)
        assert any("目前沒有" in t for t in texts)
        assert any("淨資產" in t for t in texts)
        assert any("Phase 3" in t for t in texts)


def test_provider_adapters_structured_contract_and_no_key(logged_in, monkeypatch):
    calls = []

    def fake(url, payload, headers=None):
        calls.append((url, payload, headers))
        if "ollama" in url or "11434" in url:
            return {"message": {"content": json.dumps(PARSED)}}
        return {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(PARSED)}],
                }
            ],
        }

    monkeypatch.setattr(providers, "request_json", fake)
    result = providers.parse(
        "ollama",
        "synthetic",
        "合成記帳",
        b"image",
        ollama_url="http://localhost:11434",
        openai_key="",
    )
    assert result.items[0].raw_name == "蘋果 Apples"
    assert calls[-1][1]["format"]["additionalProperties"] is False
    assert calls[-1][1]["think"] is False
    assert calls[-1][1]["messages"][1]["images"]
    assert "JSON schema:" in calls[-1][1]["messages"][0]["content"]
    providers.parse(
        "ollama",
        "synthetic",
        "",
        None,
        ollama_url="http://localhost:11434",
        openai_key="",
        prompt_version=1,
    )
    assert calls[-1][1]["messages"][0]["content"] == PROMPT_V1
    count = len(calls)
    with pytest.raises(RemoteError, match="prompt_version_unsupported"):
        providers.parse(
            "ollama",
            "synthetic",
            "",
            None,
            ollama_url="http://localhost:11434",
            openai_key="",
            prompt_version=999,
        )
    assert len(calls) == count
    result = providers.parse(
        "openai", "synthetic", "", b"image", ollama_url="", openai_key="synthetic-only"
    )
    assert result.amount == "10.50" and calls[-1][1]["store"] is False
    assert calls[-1][1]["text"]["format"]["strict"] is True
    with pytest.raises(RemoteError, match="provider_key_missing"):
        providers.parse("openai", "synthetic", "", None, ollama_url="", openai_key="")
    with pytest.raises(RemoteError, match="local_endpoint_invalid"):
        providers.parse(
            "ollama", "synthetic", "", None, ollama_url="https://unexpected.example", openai_key=""
        )
    monkeypatch.setattr(
        providers, "request_json", lambda *args: {"message": {"content": '{"amount":"NaN"}'}}
    )
    with pytest.raises(RemoteError, match="parse_invalid"):
        providers.parse(
            "ollama", "synthetic", "", None, ollama_url="http://localhost:11434", openai_key=""
        )


def test_bridge_normalization_and_bounded_download(monkeypatch):
    monkeypatch.setenv("CAPTURE_BRIDGE_TOKEN", "synthetic-bridge-token-" + "x" * 32)
    bridge = Bridge()
    monkeypatch.setattr(
        bridge, "telegram", lambda *args: {"file_path": "../../private", "file_size": 5}
    )
    with pytest.raises(RemoteError, match="telegram_file_invalid"):
        bridge.download("file")
    monkeypatch.setattr(
        bridge,
        "telegram",
        lambda *args: {"file_path": "photos/one.jpg", "file_size": 21 * 1024 * 1024},
    )
    with pytest.raises(RemoteError, match="attachment_size"):
        bridge.download("file")
    normalized = normalize(
        {
            "update_id": 1,
            "callback_query": {
                "from": {"id": 4},
                "message": {"chat": {"id": 4, "type": "private"}},
                "data": "confirm:abc:1",
            },
        },
        12345,
    )
    assert normalized["user_id"] == 4 and normalized["callback"] == "confirm:abc:1"


def test_backup_restore_preserves_unposted_receipt_and_parse_history(
    logged_in, monkeypatch, tmp_path
):
    configure(monkeypatch)
    setup(logged_in)
    headers = auth()
    row = image(logged_in)
    complete(logged_in, headers, next_job(logged_in, headers), parsed=PARSED)
    backup = create_backup()
    manifest = verify_backup(backup)
    assert (
        manifest["counts"]["capture_drafts"] == 1
        and manifest["counts"]["receipt_parse_attempts"] == 1
    )
    target = "finance_restore_" + uuid.uuid4().hex
    url = make_url(settings().database_url.get_secret_value())
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{target}"'))
    restored = create_engine(url.set(database=target))
    try:
        restore_backup(
            backup,
            url.set(database=target).render_as_string(hide_password=False),
            tmp_path / "restored",
        )
        with Session(restored) as db:
            draft = db.get(CaptureDraft, uuid.UUID(row["id"]))
            assert draft.status == "needs_review" and draft.parsed["amount"] == "10.50"
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Transaction)
                    .where(Transaction.kind == "expense")
                )
                == 0
            )
    finally:
        restored.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{target}" WITH (FORCE)'))
        admin.dispose()


def test_manual_edit_during_photo_download_preserves_file_without_replacing_edits(
    logged_in, monkeypatch
):
    configure(monkeypatch)
    _, card, _, food, _, _ = setup(logged_in)
    headers = auth()
    tg(logged_in, headers, 1, file_id="synthetic-photo")
    row = logged_in.get("/api/v1/capture/drafts").json()[0]
    row = edit(
        logged_in,
        row,
        amount="9",
        currency="USD",
        occurred_on="2026-01-05",
        account_id=card["id"],
        category_id=food["id"],
    )
    assert action(logged_in, row).json()["code"] == "draft_download_pending"
    job = next_job(logged_in, headers, "telegram_download")
    assert not job["payload"]["skip"]
    uploaded = logged_in.post(
        f"/api/v1/capture-bridge/jobs/{job['id']}/image",
        headers={**headers, "X-Job-Lease": job["lease_token"]},
        content=picture(),
    )
    assert uploaded.status_code == 200
    row = get_draft(logged_in, row)
    assert row["attachment_id"] and row["proposal"]["amount"] == "9"
    assert row["status"] == "needs_review" and next_job(logged_in, headers) is None
    assert action(logged_in, row).status_code == 200


def test_bridge_heartbeat_survives_backup_and_foreign_job_rejected(logged_in, monkeypatch):
    from app.core.db import MAINTENANCE_LOCK

    configure(monkeypatch)
    setup(logged_in)
    headers = auth()
    image(logged_in)
    job = next_job(logged_in, headers)
    with engine().connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MAINTENANCE_LOCK})
        try:
            result = logged_in.post(
                f"/api/v1/capture-bridge/jobs/{job['id']}/heartbeat",
                headers=headers,
                json={"lease_token": job["lease_token"]},
            )
            assert result.status_code == 200
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MAINTENANCE_LOCK})
    assert (
        complete(logged_in, headers, {**job, "id": str(uuid.uuid4())}, parsed=PARSED).status_code
        == 409
    )


def test_invalid_image_idempotency_conflict_cancel_and_web_draft_in_bot(logged_in, monkeypatch):
    configure(monkeypatch, "disabled")
    setup(logged_in)
    headers = auth()
    row = image(logged_in, "same-capture-key")
    conflict = logged_in.post(
        "/api/v1/capture/image?filename=synthetic.png",
        content=picture("JPEG"),
        headers={"Content-Type": "image/jpeg", "Idempotency-Key": "same-capture-key"},
    )
    assert conflict.status_code == 409
    invalid = logged_in.post(
        "/api/v1/capture/image?filename=synthetic.png",
        content=b"not an image",
        headers={"Content-Type": "image/png", "Idempotency-Key": "invalid-capture-key"},
    )
    assert invalid.status_code == 415
    tg(logged_in, headers, 1, callback=f"view:{uuid.UUID(row['id']).hex}:{row['revision']}")
    with Session(engine()) as db:
        assert db.scalar(select(Job).where(Job.kind == "telegram_send")).payload["chat_id"] == 67890
    cancelled = action(logged_in, row, "cancel")
    assert cancelled.status_code == 200 and action(logged_in, row, "cancel").status_code == 200
    assert action(logged_in, cancelled.json()).status_code == 409
    assert create_backup().is_dir()


def test_telegram_cursor_replay_after_crash_does_not_repeat_side_effects(logged_in, monkeypatch):
    import os
    import subprocess
    import sys

    configure(monkeypatch, "disabled")
    setup(logged_in)
    headers = auth()
    # A separate process commits the event, then crashes before a polling client can see its ack.
    code = """from types import SimpleNamespace
from sqlalchemy import select
from app.core.db import transaction
from app.core.models import User
from app.capture.telegram import ingest
from app.capture.schemas import TelegramUpdate
import os
with transaction() as db:
    owner=db.scalar(select(User.id))
    ingest(db,owner,TelegramUpdate(update_id=55,bot_id=12345,user_id=67890,chat_id=67890,private=True,text='合成收據 USD 10'),SimpleNamespace(state=SimpleNamespace(request_id='crash-test')))
os._exit(37)
"""
    result = subprocess.run([sys.executable, "-c", code], env=os.environ.copy())
    assert result.returncode == 37
    assert tg(logged_in, headers, 55, text="合成收據 USD 10").json()["next_offset"] == 56
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CaptureDraft)) == 1
        assert db.scalar(select(func.count()).select_from(TelegramEvent)) == 1
        assert (
            db.scalar(select(func.count()).select_from(Job).where(Job.kind == "telegram_send")) == 1
        )


def test_full_bridge_photo_to_parse_and_reply_without_network(logged_in, monkeypatch):
    from urllib.parse import urlsplit

    from app.capture import bridge as adapter

    configure(monkeypatch)
    setup(logged_in)
    before = balances(logged_in)
    headers = auth()
    monkeypatch.setenv("CAPTURE_BRIDGE_TOKEN", headers["Authorization"][7:])
    worker = Bridge()
    sent = []

    def api_call(path, payload=None):
        response = (
            logged_in.post("/api/v1/capture-bridge" + path, headers=headers, json=payload)
            if payload is not None
            else logged_in.get("/api/v1/capture-bridge" + path, headers=headers)
        )
        assert response.status_code == 200, response.text
        return response.json()

    def bytes_call(url, method="GET", data=None, headers=None, **kwargs):
        if url.startswith("https://api.telegram.org/file/"):
            return picture()
        path = urlsplit(url).path
        response = (
            logged_in.post(path, headers=headers, content=data)
            if method == "POST"
            else logged_in.get(path, headers=headers)
        )
        assert response.status_code == 200, response.text
        return response.content

    def telegram_call(method, payload):
        if method == "getFile":
            return {"file_path": "photos/synthetic.jpg", "file_size": 100}
        assert method == "sendMessage"
        sent.append(payload)
        return {"message_id": len(sent)}

    monkeypatch.setattr(worker, "api", api_call)
    monkeypatch.setattr(worker, "telegram", telegram_call)
    monkeypatch.setattr(adapter, "request_bytes", bytes_call)
    parsed_versions = []

    def parse_receipt(*args, **kwargs):
        parsed_versions.append(kwargs["prompt_version"])
        return ParsedReceipt.model_validate(PARSED)

    monkeypatch.setattr(providers, "parse", parse_receipt)
    tg(logged_in, headers, 1, file_id="synthetic-photo")
    worker.perform(next_job(logged_in, headers, "telegram_download"))
    worker.perform(next_job(logged_in, headers, "capture_parse"))
    for _ in range(3):
        job = next_job(logged_in, headers, "telegram_send")
        assert job
        worker.perform(job)
    rows = logged_in.get("/api/v1/capture/drafts").json()
    assert rows[0]["proposal"]["amount"] == "10.50" and rows[0]["attachment_id"]
    assert any("10.50" in message["text"] for message in sent)
    assert rows[0]["status"] == "needs_review" and balances(logged_in) == before
    assert parsed_versions == [PROMPT_VERSION]
    with Session(engine()) as db:
        assert db.scalar(select(ReceiptParseAttempt)).prompt_version == PROMPT_VERSION


def test_legacy_parse_job_retains_prompt_version_and_history(logged_in, monkeypatch):
    configure(monkeypatch)
    setup(logged_in)
    with Session(engine()) as db:
        before = db.scalar(select(func.count()).select_from(Transaction))
    headers = auth()
    row = image(logged_in)
    with transaction() as db:
        draft = db.get(CaptureDraft, uuid.UUID(row["id"]))
        job = db.get(Job, draft.job_id)
        assert job.payload["prompt_version"] == PROMPT_VERSION
        job.payload = {
            k: v for k, v in job.payload.items() if k not in {"prompt_version", "schema_version"}
        }
    legacy = next_job(logged_in, headers)
    assert "prompt_version" not in legacy["payload"]
    assert complete(logged_in, headers, legacy, parsed=PARSED).status_code == 200
    row = get_draft(logged_in, row)
    retry = action(logged_in, row, "retry")
    assert retry.status_code == 200
    current = next_job(logged_in, headers)
    assert current["payload"]["prompt_version"] == PROMPT_VERSION
    assert (
        complete(logged_in, headers, current, parsed={**PARSED, "subtotal": "9.99"}).status_code
        == 200
    )
    with Session(engine()) as db:
        attempts = list(
            db.scalars(select(ReceiptParseAttempt).order_by(ReceiptParseAttempt.created_at))
        )
        assert [a.prompt_version for a in attempts] == [1, PROMPT_VERSION]
        assert [a.schema_version for a in attempts] == [1, 1]
        assert [a.result["subtotal"] for a in attempts] == ["10.00", "9.99"]
        assert db.scalar(select(func.count()).select_from(Transaction)) == before


@pytest.mark.parametrize(
    "currency,source_text,accepted",
    [
        ("USD", "$10", None),
        ("USD", "Example City, USA", None),
        ("USD", "", None),
        ("USD", "USD", "USD"),
        ("TWD", "NT$", "TWD"),
        ("USD", "NT$", None),
        ("USD", "USD 10 / TWD 300", None),
    ],
)
def test_currency_requires_explicit_user_input(
    logged_in, monkeypatch, currency, source_text, accepted
):
    configure(monkeypatch)
    setup(logged_in)
    headers = auth()
    row = image(logged_in)
    job = next_job(logged_in, headers)
    with transaction() as db:
        db.get(CaptureDraft, uuid.UUID(row["id"])).source_text = source_text
    parsed = {**PARSED, "currency": currency}
    assert complete(logged_in, headers, job, parsed=parsed).status_code == 200
    current = get_draft(logged_in, row)
    assert current["proposal"]["currency"] == accepted
    assert current["parsed"]["currency"] == currency
    assert current["status"] == "needs_review"
    if accepted is None:
        assert any("AI 幣別僅供參考" in w for w in current["warnings"])


def test_transport_rate_limit_redacts_remote_body(monkeypatch):
    import io
    import urllib.error

    from app.capture import transport

    class Opener:
        def open(self, *args, **kwargs):
            raise urllib.error.HTTPError(
                "https://example.invalid/secret-token",
                429,
                "private error",
                {"Retry-After": "30"},
                io.BytesIO(b'{"parameters":{"retry_after":90},"description":"private-receipt"}'),
            )

    monkeypatch.setattr(transport.urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(RemoteError) as caught:
        transport.request_bytes("https://example.invalid/secret-token")
    assert str(caught.value) == "rate_limited" and caught.value.retry_after == 90
    assert "private" not in str(caught.value)


def test_idle_telegram_cursor_does_not_skip_randomized_lower_update(logged_in, monkeypatch):
    configure(monkeypatch, "disabled")
    setup(logged_in)
    headers = auth()
    tg(logged_in, headers, 99999, text="/help")
    with transaction() as db:
        db.scalar(select(TelegramEvent)).created_at = now() - timedelta(days=7)
    assert (
        logged_in.get("/api/v1/capture-bridge/cursor", headers=headers).json()["next_offset"] == 0
    )
    tg(logged_in, headers, 10, text="合成新收據 USD 3")
    assert (
        logged_in.get("/api/v1/capture-bridge/cursor", headers=headers).json()["next_offset"] == 11
    )
    assert len(logged_in.get("/api/v1/capture/drafts").json()) == 1
