"""Manually maintained names. Aliases never automatically classify receipt items."""

import unicodedata
import uuid

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.auth import Principal, audit, principal
from app.core.db import engine, transaction
from app.core.models import Product, ProductAlias
from app.core.schemas import StrictModel
from app.ledger.routes import protect_writes
from app.ledger.service import book_lock, fail, idempotent, owned, remember


def normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


class ProductInput(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    note: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        key = normalized(value)
        if not key or len(key) > 200 or len(value.strip()) > 200:
            raise ValueError("名稱不可空白或超過 200 字")
        return value.strip()

    @field_validator("aliases")
    @classmethod
    def valid_aliases(cls, values: list[str]) -> list[str]:
        return [cls.valid_name(v) for v in values]


class ProductUpdate(ProductInput):
    expected_revision: int = Field(ge=1)


class ProductOutput(BaseModel):
    id: uuid.UUID
    name: str
    aliases: list[str]
    note: str
    revision: int


def output(db: Session, row: Product) -> ProductOutput:
    return ProductOutput(
        id=row.id,
        name=row.name,
        note=row.note,
        revision=row.revision,
        aliases=list(
            db.scalars(
                select(ProductAlias.name)
                .where(
                    ProductAlias.owner_id == row.owner_id,
                    ProductAlias.product_id == row.id,
                    ProductAlias.normalized_name != normalized(row.name),
                )
                .order_by(ProductAlias.normalized_name)
            )
        ),
    )


def set_names(db: Session, row: Product, body: ProductInput) -> None:
    names = {normalized(v): v for v in reversed([body.name, *body.aliases])}
    if db.scalar(
        select(ProductAlias.product_id)
        .where(
            ProductAlias.owner_id == row.owner_id,
            ProductAlias.normalized_name.in_(names),
            ProductAlias.product_id != row.id,
        )
        .limit(1)
    ):
        fail("名稱或別名已屬於另一個商品，請先修正重複名稱。", "product_alias_conflict", 409)
    db.execute(
        delete(ProductAlias).where(
            ProductAlias.owner_id == row.owner_id,
            ProductAlias.product_id == row.id,
        )
    )
    db.add_all(
        [
            ProductAlias(owner_id=row.owner_id, product_id=row.id, normalized_name=key, name=value)
            for key, value in names.items()
        ]
    )
    row.name, row.note = body.name, body.note
    db.flush()


router = APIRouter(prefix="/api/v1/products/catalog", dependencies=[Depends(protect_writes)])


@router.get("", response_model=list[ProductOutput])
def catalog(user: Principal = Depends(principal)) -> list[ProductOutput]:
    with Session(engine()) as db:
        return [
            output(db, p)
            for p in db.scalars(
                select(Product)
                .where(Product.owner_id == user.owner_id)
                .order_by(Product.name, Product.id)
            )
        ]


@router.post("", response_model=ProductOutput)
def create_product(
    body: ProductInput,
    request: Request,
    user: Principal = Depends(principal),
    idempotency_key: str = Header(default=""),
) -> ProductOutput:
    with transaction() as db:
        book_lock(db, user.owner_id)
        previous, digest = idempotent(db, user.owner_id, "product.create", idempotency_key, body)
        if previous:
            return output(db, owned(db, Product, user.owner_id, previous))
        row = Product(owner_id=user.owner_id, name=body.name, note=body.note)
        db.add(row)
        db.flush()
        set_names(db, row, body)
        remember(db, user.owner_id, "product.create", idempotency_key, digest, row.id)
        audit(db, user.owner_id, "product.create", str(row.id), request)
        return output(db, row)


@router.put("/{identity}", response_model=ProductOutput)
def update_product(
    identity: uuid.UUID, body: ProductUpdate, request: Request, user: Principal = Depends(principal)
) -> ProductOutput:
    with transaction() as db:
        book_lock(db, user.owner_id)
        row = owned(db, Product, user.owner_id, identity)
        if row.revision != body.expected_revision:
            fail("商品已更新，請重新載入後編輯。", "product_revision_conflict", 409)
        set_names(db, row, body)
        row.revision += 1
        db.flush()
        audit(db, user.owner_id, "product.update", str(row.id), request)
        return output(db, row)
