from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


from pydantic import BaseModel, Field


class ShippingAddress(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=10, max_length=20)
    address_line1: str = Field(min_length=1, max_length=250)
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=1, max_length=20)
    country: str = Field(min_length=1, max_length=100)


class OrderCreate(BaseModel):
    shipping_address: ShippingAddress

class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    seller_id: UUID
    variant_id: UUID
    product_title: str
    sku: str
    unit_price: Decimal
    quantity: int
    line_total: Decimal
    status: str


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    order_number: str
    user_id: UUID
    status: str
    shipping_address: dict[str, Any]
    subtotal: Decimal
    shipping_fee: Decimal
    discount: Decimal
    total: Decimal
    placed_at: datetime
    cancelled_at: datetime | None = None
    updated_at: datetime
    estimated_delivery_date: date | None = None
    coupon_code: str | None = None
    items: list[OrderItemOut] = Field(default_factory=list)


class OrderListOut(BaseModel):
    items: list[OrderOut]
    limit: int
    offset: int
