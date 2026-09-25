import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import ApiError, Principal, audit, principal
from app.core.config import settings
from app.core.db import engine, transaction
from app.core.jobs import claim
from app.core.jobs import heartbeat as renew_lease
from app.core.models import (
    ApiToken,
    CaptureBridge,
    CaptureDraft,
    Job,
    TelegramCursor,
    TelegramEvent,
    User,
    now,
)
from app.core.security import digest
from app.ledger.routes import protect_writes
from app.ledger.service import book_lock, fail, owned
from app.receipts import service as files

from . import service, telegram
from .schemas import (
    BridgeClaim,
    BridgeJob,
    BridgeResult,
    CaptureStatus,
    DraftAction,
    DraftEdit,
    DraftOutput,
    TelegramUpdate,
    TextCapture,
)

router = APIRouter(prefix="/api/v1/capture", dependencies=[Depends(protect_writes)])
bridge = APIRouter(prefix="/api/v1/capture-bridge")


def bridge_owner(authorization: str = Header(default="")) -> uuid.UUID:
    if not authorization.startswith("Bearer ") or len(authorization) > 200:
        fail("整合授權無效。", "bridge_unauthorized", 401)
    with Session(engine()) as db:
        token = db.scalar(
            select(ApiToken).where(
                ApiToken.token_hash == digest(authorization[7:]), ApiToken.revoked_at.is_(None)
            )
        )
        if (
            not token
            or token.scopes != ["capture:bridge"]
            or token.expires_at
            and token.expires_at <= now()
        ):
            fail("整合授權已過期或撤銷。", "bridge_unauthorized", 401)
        user = db.get(User, token.owner_id)
        if not user or user.disabled_at:
            fail("整合授權無效。", "bridge_unauthorized", 401)
        return token.owner_id


async def image_body(request: Request) -> bytes:
    length = request.headers.get("content-length")
    if length and (not length.isdecimal() or int(length) > files.MAX_BYTES):
        fail("圖片最多 20 MiB。", "attachment_size", 413)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > files.MAX_BYTES:
            fail("圖片最多 20 MiB。", "attachment_size", 413)
        data.extend(chunk)
    return bytes(data)


@router.get("/status", response_model=CaptureStatus)
def status(user: Principal = Depends(principal)) -> CaptureStatus:
    cfg = settings()
    with Session(engine()) as db:
        seen = db.get(CaptureBridge, user.owner_id)
        return CaptureStatus(
            provider=cfg.capture_provider,
            model=cfg.capture_model,
            telegram_configured=bool(cfg.telegram_bot_id and cfg.telegram_user_id),
            bridge_last_seen=seen.last_seen if seen else None,
            telegram_last_received=db.scalar(
                select(TelegramEvent.created_at)
                .where(TelegramEvent.owner_id == user.owner_id, TelegramEvent.accepted.is_(True))
                .order_by(TelegramEvent.created_at.desc())
                .limit(1)
            ),
        )


@router.get("/drafts", response_model=list[DraftOutput])
def drafts(
    offset: int = Query(default=0, ge=0),
    include_closed: bool = False,
    user: Principal = Depends(principal),
) -> list[DraftOutput]:
    with Session(engine()) as db:
        query = select(CaptureDraft).where(CaptureDraft.owner_id == user.owner_id)
        if not include_closed:
            query = query.where(CaptureDraft.status.not_in(["confirmed", "cancelled"]))
        return [
            service.draft_output(db, row)
            for row in db.scalars(
                query.order_by(CaptureDraft.created_at.desc()).offset(offset).limit(25)
            )
        ]


@router.get("/drafts/{identity}", response_model=DraftOutput)
def draft(identity: uuid.UUID, user: Principal = Depends(principal)) -> DraftOutput:
    with Session(engine()) as db:
        return service.draft_output(db, owned(db, CaptureDraft, user.owner_id, identity))


@router.post("/text", response_model=DraftOutput)
def text_capture(
    body: TextCapture,
    request: Request,
    key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    user: Principal = Depends(principal),
) -> DraftOutput:
    return service.create_web(user.owner_id, key, request, text=body.text)


@router.post(
    "/image",
    response_model=DraftOutput,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def image_capture(
    request: Request,
    filename: str = Query(min_length=1, max_length=200),
    key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    user: Principal = Depends(principal),
) -> DraftOutput:
    data = await image_body(request)
    return await run_in_threadpool(
        service.create_web,
        user.owner_id,
        key,
        request,
        data=data,
        name=filename,
        mime=request.headers.get("content-type", "").split(";")[0],
    )


@router.put("/drafts/{identity}", response_model=DraftOutput)
def save(
    identity: uuid.UUID, body: DraftEdit, request: Request, user: Principal = Depends(principal)
) -> DraftOutput:
    with transaction() as db:
        row = service.save_draft(db, user.owner_id, identity, body)
        audit(db, user.owner_id, "capture.edit", str(identity), request)
        db.flush()
        return service.draft_output(db, row)


@router.post("/drafts/{identity}/confirm", response_model=DraftOutput)
def confirm(
    identity: uuid.UUID, body: DraftAction, request: Request, user: Principal = Depends(principal)
) -> DraftOutput:
    with transaction() as db:
        row = service.confirm(db, user.owner_id, identity, body, request)
        telegram.show_draft(db, row, f"confirmed:{row.id}")
        db.flush()
        return service.draft_output(db, row)


@router.post("/drafts/{identity}/cancel", response_model=DraftOutput)
def cancel(
    identity: uuid.UUID, body: DraftAction, request: Request, user: Principal = Depends(principal)
) -> DraftOutput:
    with transaction() as db:
        row = service.cancel(db, user.owner_id, identity, body.expected_revision, request)
        db.flush()
        return service.draft_output(db, row)


@router.post("/drafts/{identity}/retry", response_model=DraftOutput)
def retry(
    identity: uuid.UUID, body: DraftAction, request: Request, user: Principal = Depends(principal)
) -> DraftOutput:
    with transaction() as db:
        book_lock(db, user.owner_id)
        row = owned(db, CaptureDraft, user.owner_id, identity)
        service.editable(row, body.expected_revision)
        if row.status == "processing":
            job = db.get(Job, row.job_id) if row.job_id else None
            if job and job.status not in {"failed", "cancelled"}:
                fail("工作仍在處理或等待重試。", "draft_processing", 409)
        row.revision += 1
        job = db.get(Job, row.job_id) if row.job_id else None
        if (
            job
            and job.kind == "telegram_download"
            and not service.draft_output(db, row).attachment_id
        ):
            from app.core.jobs import enqueue

            row.status = "processing"
            row.job_id = enqueue(
                db,
                row.owner_id,
                "telegram_download",
                f"download:{row.id}:{row.revision}",
                job.payload,
            )
        else:
            service.queue_parse(db, row)
        audit(db, user.owner_id, "capture.retry", str(row.id), request)
        db.flush()
        return service.draft_output(db, row)


@bridge.get("/cursor")
def cursor(owner: uuid.UUID = Depends(bridge_owner)) -> dict[str, Any]:
    with transaction() as db:
        book_lock(db, owner)
        db.execute(
            insert(CaptureBridge)
            .values(owner_id=owner, last_seen=now())
            .on_conflict_do_update(
                index_elements=[CaptureBridge.owner_id], set_={"last_seen": now()}
            )
        )
        row = db.get(TelegramCursor, settings().telegram_bot_id)
        latest = db.scalar(
            select(TelegramEvent.created_at)
            .where(TelegramEvent.bot_id == settings().telegram_bot_id)
            .order_by(TelegramEvent.created_at.desc())
            .limit(1)
        )
        # Telegram may randomize IDs after a week without updates. Poll from zero before
        # that boundary so a new lower ID cannot be acknowledged unseen by an old offset.
        offset = row.next_offset if row and latest and latest > now() - timedelta(days=6) else 0
        return {
            "bot_id": settings().telegram_bot_id,
            "user_id": settings().telegram_user_id,
            "next_offset": offset,
        }


@bridge.post("/updates")
def update(
    body: TelegramUpdate, request: Request, owner: uuid.UUID = Depends(bridge_owner)
) -> dict[str, Any]:
    try:
        # Ledger append guards require the top-level transaction, not a savepoint.
        with transaction() as db:
            return telegram.ingest(db, owner, body, request)
    except ApiError as error:
        if error.status not in {404, 409, 422}:
            raise
        message = error.message
    except ValueError:
        message = "內容格式不正確，請輸入 /help 查看用法，或 /pending 重新開啟草稿。"
    # The failed business transaction rolls back entirely; only then persist the inbox/error.
    with transaction() as db:
        return telegram.ingest(db, owner, body, request, failure=message)


@bridge.post("/claim", response_model=BridgeJob | None)
def claim_job(body: BridgeClaim, owner: uuid.UUID = Depends(bridge_owner)) -> BridgeJob | None:
    kinds: list[str] = list(body.kinds)
    cfg = settings()
    if cfg.capture_provider == "disabled":
        kinds = [kind for kind in kinds if kind != "capture_parse"]
    if not cfg.telegram_bot_id:
        kinds = [kind for kind in kinds if not kind.startswith("telegram_")]
    with transaction() as db:
        book_lock(db, owner)
        db.execute(
            insert(CaptureBridge)
            .values(owner_id=owner, last_seen=now())
            .on_conflict_do_update(
                index_elements=[CaptureBridge.owner_id], set_={"last_seen": now()}
            )
        )
    found = claim(kinds, owner) if kinds else None
    if not found:
        return None
    job_id, token, kind = found
    with Session(engine()) as db:
        job = owned(db, Job, owner, job_id)
        payload = dict(job.payload)
        if kind in {"capture_parse", "telegram_download"}:
            row = owned(db, CaptureDraft, owner, uuid.UUID(payload["draft_id"]))
            payload["skip"] = (
                row.status in {"confirmed", "cancelled"}
                if kind == "telegram_download"
                else (row.status != "processing" or row.revision != payload["revision"])
            )
            if kind == "capture_parse":
                payload["text"] = row.source_text
                payload["has_image"] = bool(service.draft_output(db, row).attachment_id)
                payload["provider_enabled"] = (
                    payload["provider"] == cfg.capture_provider
                    and payload["model"] == cfg.capture_model
                )
        return BridgeJob(
            heartbeat_seconds=settings().lease_seconds / 3,
            id=job_id,
            lease_token=token,
            kind=kind,
            payload=payload,
        )


@bridge.post("/jobs/{identity}/heartbeat")
def heartbeat(
    identity: uuid.UUID, body: BridgeResult, owner: uuid.UUID = Depends(bridge_owner)
) -> dict[str, bool]:
    # Lease metadata, like the core worker heartbeat, remains live during a backup.
    with Session(engine()) as db:
        service.lease(db, owner, identity, body.lease_token)
    if not renew_lease(identity, body.lease_token):
        fail("工作租約已失效。", "lease_lost", 409)
    return {"ok": True}


@bridge.get("/jobs/{identity}/image")
def job_image(
    identity: uuid.UUID,
    token: uuid.UUID = Header(alias="X-Job-Lease"),
    owner: uuid.UUID = Depends(bridge_owner),
) -> Response:
    with transaction() as db:
        job = service.lease(db, owner, identity, token)
        if job.kind != "capture_parse":
            fail("工作不支援圖片讀取。", "job_kind", 400)
        row = owned(db, CaptureDraft, owner, uuid.UUID(job.payload["draft_id"]))
        attachment = service.draft_output(db, row).attachment_id
        if not attachment:
            fail("找不到收據圖片。", "not_found", 404)
    data, _, _ = files.read_content(owner, attachment, True)
    return Response(data, media_type="image/jpeg")


@bridge.post("/jobs/{identity}/image")
async def downloaded(
    identity: uuid.UUID,
    request: Request,
    token: uuid.UUID = Header(alias="X-Job-Lease"),
    owner: uuid.UUID = Depends(bridge_owner),
) -> dict[str, bool]:
    data = await image_body(request)

    def apply() -> None:
        with transaction() as db:
            book_lock(db, owner)
            job = service.lease(db, owner, identity, token)
            if job.kind != "telegram_download":
                fail("工作不支援上傳。", "job_kind", 400)
            row = owned(db, CaptureDraft, owner, uuid.UUID(job.payload["draft_id"]))
            if row.status not in {"confirmed", "cancelled"}:
                auto_parse = row.status == "processing"
                service.store_image(
                    db, row, data, job.payload["filename"], "application/octet-stream"
                )
                if auto_parse:
                    service.queue_parse(db, row)
                telegram.show_draft(db, row, f"downloaded:{row.id}:{row.revision}")
            job.status = "succeeded"
            job.lease_until = job.lease_token = None

    await run_in_threadpool(apply)
    return {"ok": True}


@bridge.post("/jobs/{identity}/result")
def result(
    identity: uuid.UUID, body: BridgeResult, owner: uuid.UUID = Depends(bridge_owner)
) -> dict[str, bool]:
    with transaction() as db:
        book_lock(db, owner)
        job = service.lease(db, owner, identity, body.lease_token)
        if job.kind == "capture_parse":
            row = owned(db, CaptureDraft, owner, uuid.UUID(job.payload["draft_id"]))
            current = row.status == "processing" and row.revision == job.payload["revision"]
            if current and not body.error_code and not body.parsed:
                fail("辨識結果未提供。", "parse_missing", 422)
            if body.parsed or body.error_code:
                service.apply_parse(db, job, body.parsed, body.error_code)
            if current and (not body.error_code or job.attempts >= 5):
                telegram.show_draft(db, row, f"parsed:{job.id}")
        if body.error_code:
            job.error_code = body.error_code
            job.status = "failed" if job.attempts >= 5 else "retry_wait"
            job.run_after = now() + timedelta(
                seconds=max(min(300, 2**job.attempts), body.retry_after)
            )
            if job.kind == "telegram_download" and job.status == "failed":
                row = owned(db, CaptureDraft, owner, uuid.UUID(job.payload["draft_id"]))
                if row.status == "processing":
                    row.status = "failed"
                    row.warnings = ["照片下載失敗，請稍後重試或重新傳送。"]
                    telegram.show_draft(db, row, f"download-failed:{job.id}")
        else:
            job.status = "succeeded"
            job.error_code = None
        job.lease_until = job.lease_token = None
    return {"ok": True}
