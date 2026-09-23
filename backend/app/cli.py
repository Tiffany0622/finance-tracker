import argparse
import getpass
import json
import os
from pathlib import Path

from sqlalchemy import select, text

from app.core.backup import create_backup, restore_backup, verify_backup
from app.core.db import check_schema, transaction
from app.core.models import BookSettings, User
from app.core.security import hasher


def initialize_user(username: str, password: str) -> None:
    if not username.strip() or len(username) > 100 or len(password) < 12 or len(password) > 1024:
        raise ValueError("帳號不可空白，密碼需為 12～1024 字元。")
    with transaction() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(724803)"))
        if db.scalar(select(User.id).limit(1)):
            raise ValueError("已有使用者，初始化不會覆寫既有帳號。")
        user = User(login_name=username, password_hash=hasher.hash(password))
        db.add(user)
        db.flush()
        db.add(BookSettings(owner_id=user.id))


def main() -> None:
    parser = argparse.ArgumentParser(description="Finance Tracker local administration")
    parser.add_argument(
        "command", choices=["init-user", "backup", "verify-backup", "restore", "openapi"]
    )
    parser.add_argument("--source", type=Path)
    parser.add_argument("--target-data", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "openapi":
        from app.main import app

        if not args.output:
            parser.error("--output required")
        args.output.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n")
        return
    check_schema()
    if args.command == "init-user":
        username = input("登入帳號：").strip()
        password = getpass.getpass("密碼（至少 12 字元）：")
        if password != getpass.getpass("再次輸入密碼："):
            raise SystemExit("密碼不一致，未變更。")
        initialize_user(username, password)
        print("使用者已建立。請登入 Web 選擇幣別與時區。")
    elif args.command == "backup":
        print(create_backup())
    elif args.command == "verify-backup":
        if not args.source:
            parser.error("--source required")
        verify_backup(args.source)
        print("備份完整性檢查通過。")
    elif args.command == "restore":
        if not args.source or not args.target_data or not os.environ.get("RESTORE_DATABASE_URL"):
            parser.error("--source, --target-data and RESTORE_DATABASE_URL required")
        restore_backup(args.source, os.environ["RESTORE_DATABASE_URL"], args.target_data)
        print("已還原至隔離環境並核對筆數與附件。外部工作未啟動；切換正式環境前仍需登入驗證。")


if __name__ == "__main__":
    main()
