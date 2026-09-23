import uuid

import pyotp
import pytest
from conftest import PASSWORD
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.cli import initialize_user
from app.core.db import engine, transaction
from app.core.jobs import enqueue
from app.core.models import AuthSession, TotpCredential, User
from app.core.security import password_ok


def test_readiness_and_no_public_registration(client: TestClient) -> None:
    assert client.get("/api/health/ready").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/register", json={}).status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/api/v1/auth/csrf").headers["cache-control"] == "no-store"


def test_initializer_never_overwrites() -> None:
    with pytest.raises(ValueError, match="已有使用者"):
        initialize_user("bob", PASSWORD)
    with Session(engine()) as db:
        assert db.scalar(select(User.login_name)) == "alice"
        encoded = db.scalar(select(User.password_hash))
        assert encoded and encoded.startswith("$argon2id$") and password_ok(encoded, PASSWORD)


def test_login_csrf_cookie_flags_and_logout(logged_in: TestClient) -> None:
    assert logged_in.get("/api/v1/auth/me").json()["username"] == "alice"
    response = logged_in.post(
        "/api/v1/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    cookies = response.headers.get_list("set-cookie")
    assert all("HttpOnly" in value and "SameSite=strict" in value for value in cookies)
    saved_access = logged_in.cookies.get("ft_access")
    assert logged_in.post("/api/v1/auth/logout").status_code == 200
    logged_in.cookies.set("ft_access", saved_access, path="/api")
    assert logged_in.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"X-CSRF-Token": "wrong"}])
def test_csrf_rejects_wrong_origin_or_token(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": PASSWORD}, headers=headers
    )
    assert response.status_code == 403


def test_refresh_replay_revokes_new_access(logged_in: TestClient) -> None:
    old_refresh = logged_in.cookies.get("ft_refresh")
    assert logged_in.post("/api/v1/auth/refresh").status_code == 200
    fresh_access = logged_in.cookies.get("ft_access")
    assert old_refresh != logged_in.cookies.get("ft_refresh")
    logged_in.cookies.clear()
    # The CSRF value is signed; same value must appear in cookie and header.
    logged_in.cookies.set("ft_csrf", logged_in.headers["X-CSRF-Token"], path="/api")
    logged_in.cookies.set("ft_refresh", old_refresh, path="/api/v1/auth")
    assert logged_in.post("/api/v1/auth/refresh").status_code == 401
    logged_in.cookies.set("ft_access", fresh_access, path="/api")
    assert logged_in.get("/api/v1/auth/me").status_code == 401
    with Session(engine()) as db:
        assert all(row.refresh_hash != old_refresh for row in db.scalars(select(AuthSession)))


def test_failed_logins_are_rate_limited_and_no_password_echo(client: TestClient) -> None:
    for _ in range(10):
        response = client.post(
            "/api/v1/auth/login", json={"username": "alice", "password": "wrong-private-value"}
        )
        assert response.status_code == 401
        assert "wrong-private-value" not in response.text
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "alice", "password": PASSWORD}
        ).status_code
        == 429
    )


def test_totp_enrollment_replay_recovery_and_disable(logged_in: TestClient) -> None:
    response = logged_in.post("/api/v1/auth/totp/setup", json={"password": PASSWORD})
    secret = response.json()["secret"]
    with Session(engine()) as db:
        credential = db.scalar(select(TotpCredential))
        assert credential and credential.encrypted_secret != secret and not credential.enabled_at
    code = pyotp.TOTP(secret).now()
    response = logged_in.post("/api/v1/auth/totp/enable", json={"password": PASSWORD, "code": code})
    assert response.status_code == 200
    recovery = response.json()["recovery_codes"]
    assert len(recovery) == len(set(recovery)) == 8
    assert logged_in.get("/api/v1/auth/me").json()["totp_enabled"]
    # The same timestep used to enable cannot be replayed at login.
    assert (
        logged_in.post(
            "/api/v1/auth/login", json={"username": "alice", "password": PASSWORD, "code": code}
        ).status_code
        == 401
    )
    assert (
        logged_in.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": PASSWORD, "code": recovery[0]},
        ).status_code
        == 200
    )
    assert (
        logged_in.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": PASSWORD, "code": recovery[0]},
        ).status_code
        == 401
    )
    assert (
        logged_in.post(
            "/api/v1/auth/totp/disable", json={"password": PASSWORD, "code": recovery[1]}
        ).status_code
        == 200
    )
    assert not logged_in.get("/api/v1/auth/me").json()["totp_enabled"]


def test_settings_revision_validation_and_private_job(logged_in: TestClient) -> None:
    initial = logged_in.get("/api/v1/auth/me").json()["settings"]
    assert initial["book_currency"] is None and not initial["setup_completed"]
    body = {"book_currency": "TWD", "timezone": "Asia/Taipei", "expected_revision": 0}
    assert logged_in.put("/api/v1/settings", json=body).status_code == 200
    assert logged_in.put("/api/v1/settings", json=body).status_code == 409
    assert (
        logged_in.put("/api/v1/settings", json={**body, "timezone": "bad/timezone"}).status_code
        == 422
    )
    with transaction() as db:
        other = User(id=uuid.uuid4(), login_name="other-test-owner", password_hash="unused")
        db.add(other)
        db.flush()
        job = enqueue(db, other.id, "probe", "private", {})
    assert logged_in.get(f"/api/v1/jobs/{job}").status_code == 404


def test_audit_is_append_only(logged_in: TestClient) -> None:
    with pytest.raises(DBAPIError, match="append-only"), engine().begin() as connection:
        connection.execute(text("UPDATE audit_logs SET action='tampered'"))
