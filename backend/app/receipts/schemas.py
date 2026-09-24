import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class AttachmentOutput(BaseModel):
    id: uuid.UUID
    receipt_id: uuid.UUID
    original_name: str
    mime: str
    size_bytes: int
    width: int
    height: int
    page_no: int
    created_at: datetime
    status: Literal["ready", "missing"]
