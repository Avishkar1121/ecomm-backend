from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

MAX_LINE_QTY = 999

# Why a line cannot be bought right now (None = fine)
CartIssue = Literal["UNAVAILABLE", "OUT_OF_STOCK", "INSUFFICIENT_STOCK"]


class CartItemAdd(BaseModel):
    product_id: UUID
    # Optional only for single-variant products; multi-variant products must send it
    # (the API answers VARIANT_REQUIRED otherwise).
    variant_id: UUID | None = None
    quantity: int = Field(default=1, ge=1, le=MAX_LINE_QTY)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=0, le=MAX_LINE_QTY)  # 0 -> remove the line item


class CartItemOut(BaseModel):
    id: UUID
    product_id: UUID
    variant_id: UUID
    quantity: int
    unit_price: float                 # live variant price (the cart stores no price of its own)
    in_stock: bool
    available_qty: int
    is_available: bool                # product + variant + seller are all still sellable
    issue: CartIssue | None = None
    line_total: float
    product_title: str | None = None
    product_slug: str | None = None
    image_url: str | None = None
    variant_name: str | None = None
    added_at: datetime


class CartOut(BaseModel):
    id: UUID
    user_id: UUID
    coupon_id: UUID | None
    items: list[CartItemOut]
    item_count: int                   # total units across purchasable lines
    has_issues: bool                  # True -> the client must fix the cart before checkout
    subtotal: float                   # purchasable lines only, at live prices
    discount: float
    total: float
    created_at: datetime
    updated_at: datetime