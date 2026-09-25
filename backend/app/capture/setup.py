"""Interactive host setup pipes a new random token on stdin; never prints a secret."""

import argparse
import sys
from datetime import timedelta

from sqlalchemy import select

from app.core.db import check_schema, transaction
from app.core.models import ApiToken, User, now
from app.core.security import digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    token = sys.stdin.readline().strip()
    if not 32 <= len(token) <= 100:
        raise SystemExit("Invalid integration token")
    check_schema()
    with transaction() as db:
        user = db.scalar(
            select(User)
            .where(User.login_name == args.username, User.disabled_at.is_(None))
            .with_for_update()
        )
        if not user:
            raise SystemExit("Account not found")
        # This deployment serves one Telegram owner. Rotating also revokes a previous owner.
        for previous in db.scalars(
            select(ApiToken).where(
                ApiToken.scopes == ["capture:bridge"], ApiToken.revoked_at.is_(None)
            )
        ):
            previous.revoked_at = now()
        db.add(
            ApiToken(
                owner_id=user.id,
                token_hash=digest(token),
                scopes=["capture:bridge"],
                expires_at=now() + timedelta(days=365),
            )
        )
    print("Integration token registered (expires in 365 days).")


if __name__ == "__main__":
    main()
