from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SellerApplicationOut(BaseModel):
    user_id: UUID
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    store_name: str
    store_slug: str
    description: str | None = None
    logo_url: str | None = None
    gstin: str | None = None
    verification_status: Literal["pending", "approved", "rejected", "suspended"]
    commission_rate: Decimal
    created_at: datetime
    updated_at: datetime
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None


class SellerApplicationPage(BaseModel):
    items: list[SellerApplicationOut]
    total: int
    page: int
    page_size: int


class SellerRejectIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=1000)


class AdminActionOut(BaseModel):
    user_id: UUID
    verification_status: Literal["approved", "rejected", "suspended"]
    reviewed_by: UUID
    reviewed_at: datetime
    rejection_reason: str | None = None
