"""Generate local environment without printing secrets or overwriting existing files."""
import base64
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    password = secrets.token_hex(24)
    values = {
        "POSTGRES_PASSWORD": password,
        "POSTGRES_ADMIN_PASSWORD": secrets.token_hex(24),
        "DATABASE_URL": f"postgresql+psycopg://finance:{password}@db:5432/finance",
        "JWT_SECRET": secrets.token_hex(32),
        "TOTP_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    }
    template = (ROOT / ".env.example").read_text()
    for key, value in values.items():
        template = template.replace(f"{key}=\n", f"{key}={value}\n")
    template = template.replace("APP_UID=10001", f"APP_UID={os.getuid()}")
    template = template.replace("APP_GID=10001", f"APP_GID={os.getgid()}")
    try:
        fd = os.open(ROOT / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(".env 已存在，未覆寫。") from None
    with os.fdopen(fd, "w") as output:
        output.write(template)
    print("已建立本機 .env（權限 600）；沒有預設登入帳號或密碼。")
