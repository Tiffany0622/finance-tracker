import secrets
import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import pyotp
from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.capture.routes import bridge as capture_bridge_router
from app.capture.routes import router as capture_router
from app.core.auth import (
    ApiError,
    Principal,
    audit,
    auth_cookies,
    clear_cookies,
    principal,
    require_csrf,
    set_csrf,
    throttle,
)
from app.core.config import settings
from app.core.db import check_schema, engine, transaction
from app.core.jobs import request_probe
from app.core.models import (
    AuthSession,
    BackupRun,
    BookSettings,
    Job,
    JournalEntry,
    SessionFamily,
    TotpCredential,
    User,
    now,
)
from app.core.schemas import (
    CsrfOutput,
    ErrorOutput,
    JobOutput,
    LoginInput,
    MessageOutput,
    RecoveryOutput,
    SettingsInput,
    SettingsOutput,
    StatusOutput,
    TotpSetupOutput,
    UserOutput,
    VerifyInput,
)
from app.core.security import (
    DUMMY_HASH,
    accept_second_factor,
    crypt,
    digest,
    new_refresh,
    new_session,
    password_ok,
    revoke_others,
)
from app.ledger.routes import router as ledger_router
from app.receipts.routes import router as receipts_router


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    check_schema()
    yield


app = FastAPI(
    lifespan=lifespan,
    responses={code: {"model": ErrorOutput} for code in (400, 401, 403, 404, 409, 422, 429, 503)},
    title="Finance Tracker",
    version="0.3.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
PREFIX = "/api/v1"

app.include_router(ledger_router)
app.include_router(receipts_router)
app.include_router(capture_router)
app.include_router(capture_bridge_router)


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    request.state.request_id = str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(ApiError)
async def api_error(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={
            "code": exc.code,
            "message": exc.message,
            "field_errors": [],
            "request_id": request.state.request_id,
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return await api_error(
        request, ApiError(exc.status_code, "http_error", "找不到此資源或不支援此操作。")
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "請檢查輸入內容。",
            "field_errors": [
                {"field": ".".join(map(str, e["loc"])), "code": e["type"]} for e in exc.errors()
            ],
            "request_id": request.state.request_id,
        },
    )


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    return await api_error(
        request, ApiError(503, "database_unavailable", "資料庫暫時無法連線，請稍後再試。")
    )


@app.get("/api/health/live", response_model=MessageOutput)
def live() -> MessageOutput:
    return MessageOutput(message="alive")


@app.get("/api/health/ready", response_model=MessageOutput)
def ready() -> MessageOutput:
    try:
        check_schema()
    except (SQLAlchemyError, RuntimeError):
        raise ApiError(503, "not_ready", "資料庫尚未就緒或需要遷移。") from None
    return MessageOutput(message="ready")


@app.get(PREFIX + "/auth/csrf", response_model=CsrfOutput)
def csrf(request: Request, response: Response) -> CsrfOutput:
    # Reuse a valid signed token so another browser tab cannot invalidate a form.
    from app.core.security import valid_csrf

    existing = request.cookies.get("ft_csrf", "")
    return CsrfOutput(token=existing if valid_csrf(existing) else set_csrf(response))


@app.post(
    PREFIX + "/auth/login", response_model=MessageOutput, dependencies=[Depends(require_csrf)]
)
def login(body: LoginInput, request: Request, response: Response) -> MessageOutput:
    throttle(request, "login", body.username.casefold())
    accepted = False
    with transaction() as db:
        user = db.scalar(select(User).where(User.login_name == body.username).with_for_update())
        valid = password_ok(
            user.password_hash if user else DUMMY_HASH, body.password.get_secret_value()
        )
        if user and not user.disabled_at and valid:
            credential = db.get(TotpCredential, user.id)
            if (
                not credential
                or not credential.enabled_at
                or accept_second_factor(credential, body.code)
            ):
                family, refresh = new_session(db, user)
                auth_cookies(response, family, refresh)
                audit(db, user.id, "login", str(family.id), request)
                accepted = True
    if not accepted:
        raise ApiError(401, "credentials_invalid", "帳號、密碼或驗證碼不正確。")
    return MessageOutput(message="已登入")


@app.post(
    PREFIX + "/auth/refresh", response_model=MessageOutput, dependencies=[Depends(require_csrf)]
)
def refresh(request: Request, response: Response) -> MessageOutput:
    token = request.cookies.get("ft_refresh", "")
    accepted = False
    with transaction() as db:
        row = db.scalar(select(AuthSession).where(AuthSession.refresh_hash == digest(token)))
        if row:
            # Serialize every rotation within a family, including replay revocation.
            family = db.scalar(
                select(SessionFamily).where(SessionFamily.id == row.family_id).with_for_update()
            )
            db.refresh(row)
            user = db.get(User, family.owner_id) if family else None
            if (
                family
                and user
                and not user.disabled_at
                and not family.revoked_at
                and family.expires_at > now()
            ):
                if row.used_at:
                    family.revoked_at = now()
                    audit(db, family.owner_id, "refresh_replay_revoked", str(family.id), request)
                else:
                    row.used_at = now()
                    auth_cookies(response, family, new_refresh(db, family))
                    accepted = True
    if not accepted:
        raise ApiError(401, "session_expired", "登入已失效，請重新登入。")
    return MessageOutput(message="已更新登入")


@app.post(
    PREFIX + "/auth/logout", response_model=MessageOutput, dependencies=[Depends(require_csrf)]
)
def logout(request: Request, response: Response) -> MessageOutput:
    with transaction() as db:
        row = db.scalar(
            select(AuthSession).where(
                AuthSession.refresh_hash == digest(request.cookies.get("ft_refresh", ""))
            )
        )
        if row:
            family = db.scalar(
                select(SessionFamily).where(SessionFamily.id == row.family_id).with_for_update()
            )
            if family:
                family.revoked_at = now()
                audit(db, family.owner_id, "logout", str(family.id), request)
    clear_cookies(response)
    return MessageOutput(message="已登出")


def settings_output(book: BookSettings) -> SettingsOutput:
    return SettingsOutput(
        book_currency=book.book_currency,
        timezone=book.timezone,
        locale=book.locale,
        settings_revision=book.settings_revision,
        setup_completed=book.setup_completed_at is not None,
    )


@app.get(PREFIX + "/auth/me", response_model=UserOutput)
def me(who: Principal = Depends(principal)) -> UserOutput:
    with Session(engine()) as db:
        book, credential = db.get(BookSettings, who.owner_id), db.get(TotpCredential, who.owner_id)
        assert book
        return UserOutput(
            username=who.username,
            totp_enabled=bool(credential and credential.enabled_at),
            settings=settings_output(book),
        )


@app.put(PREFIX + "/settings", response_model=SettingsOutput, dependencies=[Depends(require_csrf)])
def save_settings(
    body: SettingsInput, request: Request, who: Principal = Depends(principal)
) -> SettingsOutput:
    with transaction() as db:
        book = db.scalar(
            select(BookSettings).where(BookSettings.owner_id == who.owner_id).with_for_update()
        )
        assert book
        if book.settings_revision != body.expected_revision:
            raise ApiError(409, "revision_conflict", "設定已更新，請重新載入再修改。")
        if body.book_currency != book.book_currency and db.scalar(
            select(JournalEntry.id).where(JournalEntry.owner_id == who.owner_id).limit(1)
        ):
            raise ApiError(409, "book_currency_locked", "正式入帳後，基準幣別需要另外規劃遷移。")
        book.book_currency, book.timezone = body.book_currency, body.timezone
        book.settings_revision += 1
        book.setup_completed_at = now()
        audit(db, who.owner_id, "settings_updated", str(who.owner_id), request)
        return settings_output(book)


def verify_password(db: Session, who: Principal, body: VerifyInput) -> User:
    user = db.scalar(select(User).where(User.id == who.owner_id).with_for_update())
    if not user or not password_ok(user.password_hash, body.password.get_secret_value()):
        raise ApiError(401, "credentials_invalid", "密碼或驗證碼不正確。")
    return user


@app.post(
    PREFIX + "/auth/totp/setup",
    response_model=TotpSetupOutput,
    dependencies=[Depends(require_csrf)],
)
def totp_setup(
    body: VerifyInput, request: Request, who: Principal = Depends(principal)
) -> TotpSetupOutput:
    throttle(request, "totp", str(who.owner_id))
    with transaction() as db:
        verify_password(db, who, body)
        credential = db.get(TotpCredential, who.owner_id)
        if credential and credential.enabled_at:
            raise ApiError(409, "totp_enabled", "雙重驗證已啟用。")
        secret = pyotp.random_base32()
        if not credential:
            credential = TotpCredential(owner_id=who.owner_id)
            db.add(credential)
        credential.encrypted_secret = crypt().encrypt(secret.encode()).decode()
        credential.last_step = -1
        credential.recovery_hashes = []
        return TotpSetupOutput(
            secret=secret,
            provisioning_uri=pyotp.TOTP(secret).provisioning_uri(
                who.username, issuer_name="Finance Tracker"
            ),
        )


@app.post(
    PREFIX + "/auth/totp/enable",
    response_model=RecoveryOutput,
    dependencies=[Depends(require_csrf)],
)
def totp_enable(
    body: VerifyInput, request: Request, who: Principal = Depends(principal)
) -> RecoveryOutput:
    throttle(request, "totp", str(who.owner_id))
    with transaction() as db:
        verify_password(db, who, body)
        credential = db.get(TotpCredential, who.owner_id)
        if (
            not credential
            or credential.enabled_at
            or not accept_second_factor(credential, body.code)
        ):
            raise ApiError(422, "totp_invalid", "請先設定並輸入新的驗證碼。")
        codes = [secrets.token_hex(8) for _ in range(8)]
        credential.recovery_hashes = [digest("recovery:" + code) for code in codes]
        credential.enabled_at = now()
        revoke_others(db, who.owner_id, who.family_id)
        audit(db, who.owner_id, "totp_enabled", str(who.owner_id), request)
        return RecoveryOutput(recovery_codes=codes)


@app.post(
    PREFIX + "/auth/totp/disable",
    response_model=MessageOutput,
    dependencies=[Depends(require_csrf)],
)
def totp_disable(
    body: VerifyInput, request: Request, who: Principal = Depends(principal)
) -> MessageOutput:
    throttle(request, "totp", str(who.owner_id))
    with transaction() as db:
        verify_password(db, who, body)
        credential = db.get(TotpCredential, who.owner_id)
        if (
            not credential
            or not credential.enabled_at
            or not accept_second_factor(credential, body.code)
        ):
            raise ApiError(401, "credentials_invalid", "密碼或驗證碼不正確。")
        db.delete(credential)
        revoke_others(db, who.owner_id, who.family_id)
        audit(db, who.owner_id, "totp_disabled", str(who.owner_id), request)
    return MessageOutput(message="雙重驗證已停用")


def job_output(job: Job) -> JobOutput:
    return JobOutput(
        id=job.id,
        kind=job.kind,
        status=job.status,
        attempts=job.attempts,
        run_after=job.run_after,
        error_code=job.error_code,
    )


@app.post(
    PREFIX + "/jobs/probe",
    response_model=JobOutput,
    status_code=202,
    dependencies=[Depends(require_csrf)],
)
def probe(
    who: Principal = Depends(principal), idempotency_key: str = Header(min_length=8, max_length=100)
) -> JobOutput:
    with transaction() as db:
        db.scalar(select(User).where(User.id == who.owner_id).with_for_update())
        job_id = request_probe(db, who.owner_id, idempotency_key)
        db.flush()
        job = db.get(Job, job_id)
        assert job
        return job_output(job)


@app.get(PREFIX + "/jobs/{job_id}", response_model=JobOutput)
def get_job(job_id: uuid.UUID, who: Principal = Depends(principal)) -> JobOutput:
    with Session(engine()) as db:
        job = db.scalar(select(Job).where(Job.id == job_id, Job.owner_id == who.owner_id))
        if not job:
            raise ApiError(404, "not_found", "找不到工作。")
        return job_output(job)


@app.get(PREFIX + "/status", response_model=StatusOutput)
def status(who: Principal = Depends(principal)) -> StatusOutput:
    with Session(engine()) as db:
        db.execute(text("SELECT 1"))
        latest = db.scalar(select(BackupRun).order_by(BackupRun.created_at.desc()).limit(1))
        success = db.scalar(
            select(BackupRun)
            .where(BackupRun.status == "succeeded")
            .order_by(BackupRun.completed_at.desc())
            .limit(1)
        )
        successful_at = success.completed_at if success else None
        free = shutil.disk_usage(settings().data_dir).free
        return StatusOutput(
            phase="Phase 1 · 手動記帳",
            database="ready",
            disk_free_bytes=free,
            disk_low=free < 1024**3,
            backup_last_success=successful_at,
            backup_overdue=not successful_at or successful_at < now() - timedelta(hours=26),
            backup_last_error=latest.error_code if latest else None,
            backup_destination="configured_path",  # No internal path or claim that this is an external disk.
            jobs_pending=db.scalar(
                select(func.count())
                .select_from(Job)
                .where(
                    Job.owner_id == who.owner_id,
                    Job.status.in_(["queued", "running", "retry_wait"]),
                )
            )
            or 0,
            jobs_failed=db.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.owner_id == who.owner_id, Job.status == "failed")
            )
            or 0,
            integrations={
                "telegram": "configured" if settings().telegram_bot_id else "disabled",
                "cloud_ai": "configured" if settings().capture_provider == "openai" else "disabled",
                **{key: "not_implemented" for key in ("notion", "market_data", "fx")},
            },
        )
