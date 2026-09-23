import hashlib
import hmac
import secrets
import time
import uuid
from datetime import timedelta

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.models import AuthSession, SessionFamily, TotpCredential, User, now

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))


def digest(value: str) -> str:
    return hmac.new(
        settings().jwt_secret.get_secret_value().encode(), value.encode(), hashlib.sha256
    ).hexdigest()


def password_ok(encoded: str, password: str) -> bool:
    try:
        return hasher.verify(encoded, password)
    except VerificationError:
        return False


def csrf_token() -> str:
    random = secrets.token_urlsafe(32)
    return f"{random}.{digest('csrf:' + random)}"


def valid_csrf(token: str) -> bool:
    parts = token.split(".")
    return len(parts) == 2 and hmac.compare_digest(
        parts[1].encode(), digest("csrf:" + parts[0]).encode()
    )


def crypt() -> Fernet:
    return Fernet(settings().totp_key.get_secret_value().encode())


def accept_second_factor(credential: TotpCredential, code: str) -> bool:
    """Call while holding the owner/credential lock. Each code can be consumed once."""
    code_hash = digest("recovery:" + code)
    for saved in credential.recovery_hashes:
        if hmac.compare_digest(saved, code_hash):
            credential.recovery_hashes = [x for x in credential.recovery_hashes if x != saved]
            return True
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return False
    totp = pyotp.TOTP(crypt().decrypt(credential.encrypted_secret.encode()).decode())
    current = int(time.time()) // 30
    for step in (current, current - 1, current + 1):
        if step > credential.last_step and hmac.compare_digest(totp.at(step * 30), code):
            credential.last_step = step
            return True
    return False


def new_session(db: Session, user: User) -> tuple[SessionFamily, str]:
    family = SessionFamily(
        owner_id=user.id, expires_at=now() + timedelta(days=settings().session_days)
    )
    db.add(family)
    db.flush()
    return family, new_refresh(db, family)


def new_refresh(db: Session, family: SessionFamily) -> str:
    token = secrets.token_urlsafe(48)
    db.add(AuthSession(family_id=family.id, refresh_hash=digest(token)))
    return token


def access_token(family: SessionFamily) -> str:
    return jwt.encode(
        {
            "sub": str(family.owner_id),
            "sid": str(family.id),
            "iat": now(),
            "exp": now() + timedelta(minutes=settings().access_minutes),
            "iss": "finance-tracker",
            "aud": "finance-web",
        },
        settings().jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_access(token: str) -> dict[str, str]:
    return jwt.decode(
        token,
        settings().jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        issuer="finance-tracker",
        audience="finance-web",
        options={"require": ["sub", "sid", "exp", "iat"]},
    )


def revoke_others(db: Session, owner: uuid.UUID, keep: uuid.UUID) -> None:
    for family in db.scalars(
        select(SessionFamily).where(SessionFamily.owner_id == owner).with_for_update()
    ):
        if family.id != keep:
            family.revoked_at = now()
