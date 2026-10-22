from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.catlog import Page  # noqa: F401 — re-exported for the router


class WishlistItemAdd(BaseModel):
    product_id: UUID


class WishlistItemOut(BaseModel):
    id: UUID
    product_id: UUID
    product_title: str | None = None
    product_slug: str | None = None
    image_url: str | None = None
    price: float | None = None
    in_stock: bool | None = None
    created_at: datetime