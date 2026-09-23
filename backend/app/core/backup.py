import hashlib
import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import MAINTENANCE_LOCK, SCHEMA_VERSION, engine, transaction
from app.core.models import BackupRun, now


def sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def pg_environment(url: str) -> dict[str, str]:
    parsed = make_url(url)
    return {
        **os.environ,
        "PGHOST": parsed.host or "localhost",
        "PGPORT": str(parsed.port or 5432),
        "PGUSER": parsed.username or "",
        "PGPASSWORD": parsed.password or "",
        "PGDATABASE": parsed.database or "",
        "PGCONNECT_TIMEOUT": "10",
    }


def pg_run(args: list[str], url: str) -> None:
    result = subprocess.run(args, env=pg_environment(url), capture_output=True, timeout=1800)
    if result.returncode:
        # pg stderr may contain credentials or names; keep it out of job errors/logs.
        raise RuntimeError("postgres_backup_command_failed")


def verify_backup(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not (path / "manifest.json").is_file():
        raise ValueError("backup_incomplete")
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest.get("format_version") != 1 or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("backup_version_unsupported")
    actual = set()
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ValueError("backup_symlink_rejected")
        if item.is_file() and item != path / "manifest.json":
            actual.add(str(item.relative_to(path)))
    if actual != set(manifest["files"]):
        raise ValueError("backup_inventory_mismatch")
    for name, expected in manifest["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("backup_path_invalid")
        file = path / relative
        if (
            file.is_symlink()
            or not file.resolve().is_relative_to(path.resolve())
            or not file.is_file()
        ):
            raise ValueError("backup_file_invalid")
        if sha256(file) != expected:
            raise ValueError("backup_checksum_mismatch")
    if "database.dump" not in manifest["files"]:
        raise ValueError("backup_database_missing")
    return manifest  # type: ignore[no-any-return]


@contextmanager
def backup_guard(backup_id: uuid.UUID) -> Iterator[None]:
    # Separate namespace from the global maintenance/scheduler locks.
    key = int.from_bytes(backup_id.bytes[:4], "big", signed=True)
    with engine().connect() as guard:
        guard.execute(text("SELECT pg_advisory_lock(724804, :key)"), {"key": key})
        try:
            yield
        finally:
            guard.execute(text("SELECT pg_advisory_unlock(724804, :key)"), {"key": key})


def create_backup(backup_id: uuid.UUID | None = None) -> Path:
    backup_id = backup_id or uuid.uuid4()
    with backup_guard(backup_id):
        return _create_backup(backup_id)


def _create_backup(backup_id: uuid.UUID) -> Path:
    cfg = settings()
    backup_id = backup_id or uuid.uuid4()
    cfg.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = cfg.backup_dir / str(backup_id)
    with transaction() as db:
        existing = db.get(BackupRun, backup_id)
        if not existing:
            db.add(
                BackupRun(
                    id=backup_id,
                    status="running",
                    target=str(backup_id),
                    app_commit=cfg.app_commit,
                    schema_version=SCHEMA_VERSION,
                )
            )
        else:
            existing.status, existing.error_code = "running", None
    try:
        if not destination.exists():
            staging = cfg.backup_dir / (str(backup_id) + ".incomplete")
            if staging.exists():
                # Only this job's incomplete artifact, never a completed backup.
                shutil.rmtree(staging)
            staging.mkdir(mode=0o700)
            with engine().connect() as guard:
                guard.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MAINTENANCE_LOCK})
                try:
                    pg_run(
                        [
                            "pg_dump",
                            "--format=custom",
                            "--no-owner",
                            "--no-acl",
                            f"--file={staging / 'database.dump'}",
                        ],
                        cfg.database_url.get_secret_value(),
                    )
                    attachments = cfg.data_dir / "attachments"
                    if attachments.exists():
                        if (
                            any(p.is_symlink() for p in attachments.rglob("*"))
                            or attachments.is_symlink()
                        ):
                            raise ValueError("attachment_symlink_rejected")
                        shutil.copytree(attachments, staging / "attachments")
                    with Session(engine()) as db:
                        counts = {
                            name: db.scalar(text(f'SELECT count(*) FROM "{name}"'))
                            for name in ("users", "book_settings", "currencies", "totp_credentials")
                        }
                        db_version = db.scalar(text("SHOW server_version"))
                    files = {
                        str(p.relative_to(staging)): sha256(p)
                        for p in staging.rglob("*")
                        if p.is_file()
                    }
                    manifest = {
                        "format_version": 1,
                        "schema_version": SCHEMA_VERSION,
                        "app_commit": cfg.app_commit,
                        "postgres_version": db_version,
                        "backup_id": str(backup_id),
                        "created_at": now().isoformat(),
                        "counts": counts,
                        "files": files,
                        "secrets": "Keep JWT_SECRET and TOTP_KEY separately; not included as plaintext.",
                    }
                    # Written last, followed by atomic directory publication on this filesystem.
                    (staging / "manifest.json").write_text(
                        json.dumps(manifest, indent=2, ensure_ascii=False)
                    )
                    for path in staging.rglob("*"):
                        path.chmod(0o700 if path.is_dir() else 0o600)
                    verify_backup(staging)
                    staging.rename(destination)
                finally:
                    guard.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": MAINTENANCE_LOCK}
                    )
            # A complete directory also lets a retried job reconcile an uncertain success.
        manifest = verify_backup(destination)
        with transaction() as db:
            run = db.get(BackupRun, backup_id)
            assert run
            run.status = "succeeded"
            # Retrying an old published backup must not make its data look fresh.
            run.completed_at = datetime.fromisoformat(manifest["created_at"])
            run.manifest_hash = sha256(destination / "manifest.json")
        prune_backups()
        return destination
    except Exception:
        with transaction() as db:
            run = db.get(BackupRun, backup_id)
            assert run
            run.status, run.error_code = "failed", "backup_failed"
        raise


def prune_backups() -> None:
    """Only delete successful, verified artifacts older than 30 days; preserve latest."""
    with transaction() as db:
        runs = list(
            db.scalars(
                select(BackupRun)
                .where(BackupRun.status == "succeeded")
                .order_by(BackupRun.completed_at.desc())
            )
        )
        for run in runs[1:]:
            if run.completed_at and run.completed_at < now() - timedelta(days=30):
                path = settings().backup_dir / str(run.id)
                if path.exists():
                    verify_backup(path)
                    shutil.rmtree(path)
                    run.status = "expired"


def restore_backup(source: Path, target_url: str, target_data: Path) -> None:
    """Restore only to an empty, separately named database and attachment directory."""
    from sqlalchemy import create_engine

    manifest = verify_backup(source)
    original, target = make_url(settings().database_url.get_secret_value()), make_url(target_url)
    if target.database == original.database or not (target.database or "").startswith(
        "finance_restore_"
    ):
        raise ValueError("restore_requires_separate_finance_restore_database")
    if target_data.resolve() == settings().data_dir.resolve() or (
        target_data.exists() and any(target_data.iterdir())
    ):
        raise ValueError("restore_requires_empty_data_directory")
    restore_engine = create_engine(target_url)
    try:
        with restore_engine.connect() as conn:
            if conn.scalar(
                text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
            ):
                raise ValueError("restore_requires_empty_database")
        pg_run(
            [
                "pg_restore",
                "--dbname",
                target.database or "",
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
                "--single-transaction",
                str(source / "database.dump"),
            ],
            target_url,
        )
        target_data.mkdir(parents=True, exist_ok=True, mode=0o700)
        if (source / "attachments").exists():
            shutil.copytree(source / "attachments", target_data / "attachments")
        with restore_engine.connect() as conn:
            if conn.scalar(text("SELECT version_num FROM alembic_version")) != SCHEMA_VERSION:
                raise ValueError("restored_schema_mismatch")
            for name, expected in manifest["counts"].items():
                if name not in {"users", "book_settings", "currencies", "totp_credentials"}:
                    raise ValueError("backup_count_table_invalid")
                if conn.scalar(text(f'SELECT count(*) FROM "{name}"')) != expected:
                    raise ValueError("restored_count_mismatch")
        for name, expected in manifest["files"].items():
            if name.startswith("attachments/") and sha256(target_data / name) != expected:
                raise ValueError("restored_attachment_mismatch")
        with transaction() as db:
            run = db.get(BackupRun, uuid.UUID(manifest["backup_id"]))
            if run:
                run.last_restore_verified_at = now()
    finally:
        restore_engine.dispose()
