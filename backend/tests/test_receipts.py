import io
import os
import struct
import subprocess
import sys
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_ledger import expense, post, report, setup

from app.core.backup import create_backup, verify_backup
from app.core.config import settings
from app.core.db import engine, transaction
from app.core.models import Attachment, Receipt, ReceiptAttachment, TransactionAttachment, User, now
from app.receipts import service


def picture(fmt="PNG", orientation=None):
    image = Image.new("RGB", (80, 120), "#ddeedd")
    output = io.BytesIO()
    exif = Image.Exif()
    if orientation:
        exif[274] = orientation
        exif[315] = "synthetic-private-metadata"
    image.save(output, format=fmt, exif=exif)
    return output.getvalue()


def upload(client, txn, data=None, mime="image/png", name="收據.png", key=None):
    return client.post(
        f"/api/v1/transactions/{txn}/attachments",
        params={"filename": name},
        content=picture() if data is None else data,
        headers={"Content-Type": mime, "Idempotency-Key": key or str(uuid.uuid4())},
    )


def purchase(client):
    _, card, _, food, _, _ = setup(client)
    return post(client, "/transactions", expense(card, food))


def test_multi_image_original_preview_order_and_unchanged_ledger(logged_in):
    txn = purchase(logged_in)
    before = report(logged_in)["document"]["metrics"]
    source = picture("JPEG", 6)
    one = upload(logged_in, txn["id"], source, "image/jpeg", "../\\收據\r\n.jpg", "repeat-key-123")
    assert one.status_code == 200, one.text
    row = one.json()
    assert row["width"] == 120 and row["height"] == 80
    assert row["original_name"] == "收據.jpg"
    assert (
        upload(logged_in, txn["id"], source, "image/jpeg", "收據.jpg", "repeat-key-123").json()[
            "id"
        ]
        == row["id"]
    )
    assert upload(logged_in, txn["id"], key="repeat-key-123").status_code == 409
    two = upload(logged_in, txn["id"])
    assert two.status_code == 200
    rows = logged_in.get(f"/api/v1/transactions/{txn['id']}/attachments").json()
    assert [r["page_no"] for r in rows] == [1, 2]
    assert rows[0]["receipt_id"] == rows[1]["receipt_id"]
    original = logged_in.get(f"/api/v1/attachments/{row['id']}/original")
    assert original.content == source
    assert "attachment;" in original.headers["content-disposition"]
    assert "filename*=UTF-8''" in original.headers["content-disposition"]
    assert original.headers["cache-control"] == "no-store"
    preview = logged_in.get(f"/api/v1/attachments/{row['id']}/preview")
    assert preview.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(preview.content)) as decoded:
        assert decoded.size == (120, 80) and not decoded.getexif()
        assert b"synthetic-private-metadata" not in preview.content
    assert report(logged_in)["document"]["metrics"] == before
    # Corrections keep the same attachment association; voiding keeps the originals readable.
    changed = {
        **expense({"id": txn["account_id"]}, {"id": txn["splits"][0]["category_id"]}),
        "amount": "90",
        "reason": "修正",
        "expected_revision": 1,
    }
    response = logged_in.put(
        f"/api/v1/transactions/{txn['id']}",
        json=changed,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 200, response.text
    assert len(logged_in.get(f"/api/v1/transactions/{txn['id']}/attachments").json()) == 2
    post(logged_in, f"/transactions/{txn['id']}/void", {"reason": "測試", "expected_revision": 2})
    assert logged_in.get(f"/api/v1/attachments/{row['id']}/original").content == source
    assert upload(logged_in, txn["id"]).status_code == 409


def test_heic_roundtrip_and_compatible_jpeg_preview(logged_in):
    txn = purchase(logged_in)
    source = picture("HEIF")
    result = upload(logged_in, txn["id"], source, "image/heic", "synthetic.heic")
    assert result.status_code == 200, result.text
    row = result.json()
    assert row["mime"] == "image/heic" and row["width"] == 80 and row["height"] == 120
    assert logged_in.get(f"/api/v1/attachments/{row['id']}/original").content == source
    preview = logged_in.get(f"/api/v1/attachments/{row['id']}/preview")
    with Image.open(io.BytesIO(preview.content)) as decoded:
        assert decoded.format == "JPEG" and decoded.size == (80, 120)


def test_rejects_invalid_type_corruption_bombs_and_stream_overflow(logged_in):
    txn = purchase(logged_in)
    for data, mime in [
        (b"<script>alert(1)</script>", "image/png"),
        (b"%PDF-1.7", "application/pdf"),
        (picture("GIF"), "image/gif"),
        (picture(), "image/jpeg"),
        (picture("JPEG")[:100], "image/jpeg"),
    ]:
        assert upload(logged_in, txn["id"], data, mime).status_code == 415
    assert upload(logged_in, txn["id"], b"").status_code == 413

    # A tiny PNG declaring 60 million pixels must be rejected before allocating decoded pixels.
    def chunk(kind, body):
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    bomb = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 10000, 6000, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", b"x")
    )
    rejected = upload(logged_in, txn["id"], bomb)
    assert rejected.status_code == 422 and rejected.json()["code"] == "attachment_pixels"
    response = logged_in.post(
        f"/api/v1/transactions/{txn['id']}/attachments?filename=large.png",
        content=iter([b"x" * (1024 * 1024)] * 21),
        headers={"Idempotency-Key": "large-upload", "Content-Type": "image/png"},
    )
    assert response.status_code == 413
    assert logged_in.get(f"/api/v1/transactions/{txn['id']}/attachments").json() == []


def test_owner_auth_and_csrf_protect_every_attachment_route(logged_in, client):
    txn = purchase(logged_in)
    row = upload(logged_in, txn["id"]).json()
    base = f"/api/v1/transactions/{txn['id']}/attachments"
    assert (
        logged_in.delete(base + "/" + row["id"], headers={"X-CSRF-Token": "invalid"}).status_code
        == 403
    )
    assert upload(logged_in, txn["id"], data=picture()).status_code == 200
    from app.core.security import hasher

    with transaction() as db:
        db.add(User(login_name="bob", password_hash=hasher.hash("synthetic-test-passphrase")))
    assert (
        logged_in.post(
            "/api/v1/auth/login", json={"username": "bob", "password": "synthetic-test-passphrase"}
        ).status_code
        == 200
    )
    for path in [
        base,
        f"/api/v1/attachments/{row['id']}/preview",
        f"/api/v1/attachments/{row['id']}/original",
    ]:
        assert logged_in.get(path).status_code == 404
    assert upload(logged_in, txn["id"]).status_code == 404
    assert logged_in.delete(base + "/" + row["id"]).status_code == 404
    logged_in.post("/api/v1/auth/logout")
    assert logged_in.get(f"/api/v1/attachments/{row['id']}/original").status_code == 401


def test_concurrent_retry_saves_one_file_and_one_page(logged_in):
    txn = purchase(logged_in)
    with Session(engine()) as db:
        owner = db.scalar(select(User.id))
    request = SimpleNamespace(state=SimpleNamespace(request_id="synthetic-test"))

    def send(_):
        return service.store_upload(
            owner,
            uuid.UUID(txn["id"]),
            "concurrent-key",
            "test.png",
            picture(),
            "image/png",
            request,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, range(2)))
    assert results[0].id == results[1].id
    with Session(engine()) as db:
        for model in (Attachment, Receipt, TransactionAttachment, ReceiptAttachment):
            assert db.scalar(select(func.count()).select_from(model)) == 1
    assert len(list((settings().data_dir / "attachments/objects").iterdir())) == 1


def test_deletion_gc_and_no_ledger_effect(logged_in):
    txn = purchase(logged_in)
    before = report(logged_in)["document"]["metrics"]
    row = upload(logged_in, txn["id"], key="removal-key").json()
    path = f"/api/v1/transactions/{txn['id']}/attachments/{row['id']}"
    assert logged_in.delete(path).status_code == 200
    assert logged_in.delete(path).status_code == 200
    assert logged_in.get(f"/api/v1/attachments/{row['id']}/original").status_code == 404
    assert upload(logged_in, txn["id"], key="removal-key").status_code == 409
    assert report(logged_in)["document"]["metrics"] == before
    with transaction() as db:
        item = db.get(Attachment, uuid.UUID(row["id"]))
        item.deleted_at = now() - timedelta(days=2)
        folder = service.file_path(item.storage_key, "original").parent
    assert service.collect_garbage() == 0  # Recently touched files stay protected.
    os.utime(folder, (0, 0))
    assert service.collect_garbage() == 1 and not folder.exists()
    assert service.collect_garbage() == 0
    backup = create_backup()
    assert not any("/objects/" in name for name in verify_backup(backup)["files"])


def test_crash_after_rename_before_commit_recovers_safely(logged_in):
    txn = purchase(logged_in)
    with Session(engine()) as db:
        owner = db.scalar(select(User.id))
    code = """
import io,os,uuid
from types import SimpleNamespace
from PIL import Image
from app.receipts import service
original_publish=service.publish
def crash(*args):
    original_publish(*args)
    os._exit(37)
service.publish=crash
b=io.BytesIO(); Image.new('RGB',(12,12),'white').save(b,format='PNG')
service.store_upload(uuid.UUID(os.environ['SYNTHETIC_OWNER']),uuid.UUID(os.environ['SYNTHETIC_TXN']),
 'crash-key','test.png',b.getvalue(),'image/png',SimpleNamespace(state=SimpleNamespace(request_id='test')))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "SYNTHETIC_OWNER": str(owner), "SYNTHETIC_TXN": txn["id"]},
    )
    assert result.returncode == 37
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Attachment)) == 0
        assert db.scalar(select(func.count()).select_from(Receipt)) == 0
    root = settings().data_dir / "attachments"
    orphan = next((root / "objects").iterdir())
    stage = root / "staging" / str(uuid.uuid4())
    stage.mkdir()
    (stage / "partial").write_bytes(b"incomplete")
    kept = upload(logged_in, txn["id"], key="crash-key")
    assert kept.status_code == 200
    for folder in (orphan, stage):
        os.utime(folder, (0, 0))
    assert service.collect_garbage() == 2
    assert logged_in.get(f"/api/v1/attachments/{kept.json()['id']}/preview").status_code == 200


def test_missing_files_symlinks_and_corruption_fail_closed(logged_in, tmp_path):
    txn = purchase(logged_in)
    row = upload(logged_in, txn["id"]).json()
    with Session(engine()) as db:
        item = db.get(Attachment, uuid.UUID(row["id"]))
        path = service.file_path(item.storage_key, "original")
    data = path.read_bytes()
    path.unlink()
    assert (
        logged_in.get(f"/api/v1/transactions/{txn['id']}/attachments").json()[0]["status"]
        == "missing"
    )
    assert logged_in.get(f"/api/v1/attachments/{row['id']}/original").status_code == 409
    with pytest.raises(ValueError, match="missing"):
        create_backup()
    outside = tmp_path / "private"
    outside.write_bytes(data)
    path.symlink_to(outside)
    assert logged_in.get(f"/api/v1/attachments/{row['id']}/original").status_code == 409
    with pytest.raises(ValueError, match="symlink"):
        create_backup()
    path.unlink()
    path.write_bytes(b"changed")
    assert logged_in.get(f"/api/v1/attachments/{row['id']}/original").status_code == 409
    with pytest.raises(ValueError, match="changed"):
        create_backup()
    assert outside.read_bytes() == data


def test_attachment_count_limit_can_be_released_without_duplicate_page_numbers(logged_in):
    txn = purchase(logged_in)
    rows = [upload(logged_in, txn["id"]).json() for _ in range(20)]
    rejected = upload(logged_in, txn["id"])
    assert rejected.status_code == 409 and rejected.json()["code"] == "attachment_limit"
    assert (
        logged_in.delete(
            f"/api/v1/transactions/{txn['id']}/attachments/{rows[0]['id']}"
        ).status_code
        == 200
    )
    result = upload(logged_in, txn["id"])
    assert result.status_code == 200 and result.json()["page_no"] == 21
    assert len(logged_in.get(f"/api/v1/transactions/{txn['id']}/attachments").json()) == 20


def test_backup_maintenance_lock_fences_file_mutations(logged_in):
    import threading
    import time

    from sqlalchemy import text

    from app.core.db import MAINTENANCE_LOCK

    txn = purchase(logged_in)
    with Session(engine()) as db:
        owner = db.scalar(select(User.id))
    started = threading.Event()

    def write():
        started.set()
        return service.store_upload(
            owner,
            uuid.UUID(txn["id"]),
            "guarded-upload",
            "test.png",
            picture(),
            "image/png",
            SimpleNamespace(state=SimpleNamespace(request_id="test")),
        )

    with engine().connect() as guard, ThreadPoolExecutor(max_workers=1) as pool:
        guard.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MAINTENANCE_LOCK})
        try:
            future = pool.submit(write)
            assert started.wait(2)
            time.sleep(0.15)
            assert not future.done()
            assert not (settings().data_dir / "attachments/objects").exists()
        finally:
            guard.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MAINTENANCE_LOCK})
        row = future.result(timeout=5)
        assert row.status == "ready"


def test_storage_failure_is_retriable_and_orphans_are_not_backed_up(logged_in, monkeypatch):
    txn = purchase(logged_in)
    publish = service.publish

    def fail_after_publish(*args):
        publish(*args)
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(service, "publish", fail_after_publish)
    failed = upload(logged_in, txn["id"], key="storage-failure-key")
    assert failed.status_code == 503
    assert failed.json()["code"] == "attachment_storage_unavailable"
    assert logged_in.get(f"/api/v1/transactions/{txn['id']}/attachments").json() == []
    monkeypatch.setattr(service, "publish", publish)
    success = upload(logged_in, txn["id"], key="storage-failure-key")
    assert success.status_code == 200
    backup = create_backup()
    files = verify_backup(backup)["files"]
    assert len([f for f in files if f.endswith("/original")]) == 1
    assert len(list((settings().data_dir / "attachments/objects").iterdir())) == 2
