from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.deps import require_role
from app.schemas.orders import OrderCreate, OrderListOut, OrderOut
from app.models.orders import Order
from app.services.orders_service import orderService

router = APIRouter(prefix="/orders", tags=["orders"])
buyer_only = require_role("buyer")


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def place_order(
    payload: OrderCreate,
    response: Response,
    db: Session = Depends(get_db),
    user: dict = Depends(buyer_only),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=100)] = None,
):
    already_exists = bool(idempotency_key and db.scalar(select(Order.id).where(Order.user_id == user["id"], Order.idempotency_key == idempotency_key)))
    order = orderService.place_order(db, user_id=user["id"], shipping_address=payload.shipping_address.model_dump(), idempotency_key=idempotency_key)
    response.status_code = status.HTTP_200_OK if already_exists else status.HTTP_201_CREATED
    return order


@router.get("", response_model=OrderListOut)
def list_orders(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: dict = Depends(buyer_only),
):
    return {"items": orderService.list_orders(db, user_id=user["id"], limit=limit, offset=offset), "limit": limit, "offset": offset}


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: UUID, db: Session = Depends(get_db), user: dict = Depends(buyer_only)):
    return orderService.get_order(db, user_id=user["id"], order_id=order_id)
