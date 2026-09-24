import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, principal
from app.core.db import engine
from app.core.models import Transaction
from app.core.schemas import ErrorOutput, MessageOutput
from app.ledger.routes import protect_writes
from app.ledger.service import fail, owned
from app.receipts import service
from app.receipts.schemas import AttachmentOutput

router = APIRouter(prefix="/api/v1", dependencies=[Depends(protect_writes)])


def check_owner(owner: uuid.UUID, identity: uuid.UUID) -> None:
    with Session(engine()) as db:
        owned(db, Transaction, owner, identity)


@router.get("/transactions/{identity}/attachments", response_model=list[AttachmentOutput])
def attachments(
    identity: uuid.UUID, user: Principal = Depends(principal)
) -> list[AttachmentOutput]:
    with Session(engine()) as db:
        return service.list_attachments(db, user.owner_id, identity)


@router.post(
    "/transactions/{identity}/attachments",
    response_model=AttachmentOutput,
    responses={413: {"model": ErrorOutput}, 415: {"model": ErrorOutput}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def upload(
    identity: uuid.UUID,
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    user: Principal = Depends(principal),
) -> AttachmentOutput:
    await run_in_threadpool(check_owner, user.owner_id, identity)
    length = request.headers.get("content-length")
    if length and (not length.isdecimal() or int(length) > service.MAX_BYTES):
        fail("每張圖片最多 20 MiB。", "attachment_size", 413)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > service.MAX_BYTES:
            fail("每張圖片最多 20 MiB。", "attachment_size", 413)
        data.extend(chunk)
    mime = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return await run_in_threadpool(
        service.store_upload, user.owner_id, identity, key, filename, bytes(data), mime, request
    )


@router.get(
    "/attachments/{identity}/preview",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {"schema": {"type": "string", "format": "binary"}}}}
    },
)
def preview(identity: uuid.UUID, user: Principal = Depends(principal)) -> Response:
    data, mime, _ = service.read_content(user.owner_id, identity, True)
    return Response(
        data, media_type=mime, headers={"Content-Security-Policy": "default-src 'none'; sandbox"}
    )


@router.get(
    "/attachments/{identity}/original",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            }
        }
    },
)
def original(identity: uuid.UUID, user: Principal = Depends(principal)) -> Response:
    data, mime, name = service.read_content(user.owner_id, identity, False)
    extension = service.EXTENSIONS[mime]
    name = (name.rsplit(".", 1)[0] or "receipt") + "." + extension
    return Response(
        data,
        media_type=mime,
        headers={
            "Content-Disposition": f"attachment; filename=\"receipt-{identity}.{extension}\"; filename*=UTF-8''{quote(name, safe='')}",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.delete("/transactions/{txn_id}/attachments/{identity}", response_model=MessageOutput)
def remove(
    txn_id: uuid.UUID, identity: uuid.UUID, request: Request, user: Principal = Depends(principal)
) -> MessageOutput:
    service.remove_attachment(user.owner_id, txn_id, identity, request)
    return MessageOutput(message="附件已移除，交易金額保持不變。")
