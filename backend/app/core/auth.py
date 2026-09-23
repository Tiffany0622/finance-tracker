import hmac
import uuid
from dataclasses import dataclass
from datetime import timedelta

import jwt
from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import engine, transaction
from app.core.models import AuditLog, LoginBucket, SessionFamily, User, now
from app.core.security import access_token, csrf_token, decode_access, digest, valid_csrf


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class Principal:
    owner_id: uuid.UUID
    family_id: uuid.UUID
    username: str


def require_csrf(request: Request) -> None:
    cookie = request.cookies.get("ft_csrf", "")
    header = request.headers.get("x-csrf-token", "")
    if (
        request.headers.get("origin") != settings().app_origin
        or not cookie
        or not hmac.compare_digest(cookie.encode(), header.encode())
        or not valid_csrf(cookie)
    ):
        raise ApiError(403, "csrf_invalid", "驗證已失效，請重新整理頁面。")


def principal(request: Request) -> Principal:
    try:
        claims = decode_access(request.cookies.get("ft_access", ""))
        owner, family_id = uuid.UUID(claims["sub"]), uuid.UUID(claims["sid"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise ApiError(401, "login_required", "請重新登入。") from None
    with Session(engine()) as db:
        family = db.get(SessionFamily, family_id)
        user = db.get(User, owner)
        if (
            not family
            or family.owner_id != owner
            or family.revoked_at
            or family.expires_at <= now()
            or not user
            or user.disabled_at
        ):
            raise ApiError(401, "login_required", "請重新登入。")
        return Principal(owner, family_id, user.login_name)


def auth_cookies(response: Response, family: SessionFamily, refresh: str) -> None:
    cfg = settings()
    lifetime = max(0, int((family.expires_at - now()).total_seconds()))
    response.set_cookie(
        "ft_access",
        access_token(family),
        max_age=cfg.access_minutes * 60,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="strict",
        path="/api",
    )
    response.set_cookie(
        "ft_refresh",
        refresh,
        max_age=lifetime,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="strict",
        path="/api/v1/auth",
    )


def set_csrf(response: Response) -> str:
    token = csrf_token()
    response.set_cookie(
        "ft_csrf",
        token,
        httponly=True,
        secure=settings().cookie_secure,
        samesite="strict",
        path="/api",
        max_age=7 * 86400,
    )
    return token


def clear_cookies(response: Response) -> None:
    for name, path in (("ft_access", "/api"), ("ft_refresh", "/api/v1/auth"), ("ft_csrf", "/api")):
        response.delete_cookie(
            name, path=path, secure=settings().cookie_secure, httponly=True, samesite="strict"
        )


def throttle(request: Request, scope: str, identity: str) -> None:
    """Persist attempts outside the authentication transaction, including failures."""
    ip = request.client.host if request.client else "unknown"
    limited = False
    with transaction() as db:
        for key in sorted(
            {digest(f"limit:{scope}:ip:{ip}"), digest(f"limit:{scope}:user:{identity}")}
        ):
            db.execute(
                insert(LoginBucket)
                .values(key=key, attempts=0, expires_at=now() + timedelta(minutes=15))
                .on_conflict_do_nothing(index_elements=[LoginBucket.key])
            )
            bucket = db.scalar(select(LoginBucket).where(LoginBucket.key == key).with_for_update())
            assert bucket
            if bucket.expires_at <= now():
                bucket.attempts = 0
                bucket.expires_at = now() + timedelta(minutes=15)
            bucket.attempts += 1
            limited |= bucket.attempts > 10
    if limited:
        raise ApiError(429, "rate_limited", "嘗試次數過多，請於 15 分鐘後再試。")


def audit(db: Session, owner: uuid.UUID, action: str, entity: str, request: Request) -> None:
    db.add(
        AuditLog(
            owner_id=owner,
            action=action,
            entity_type="core",
            entity_id=entity,
            request_id=request.state.request_id,
            redacted_diff={},
        )
    )
