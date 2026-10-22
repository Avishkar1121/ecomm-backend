from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import api_error
from app.deps import require_role
from app.schemas.cart import CartItemAdd, CartItemUpdate, CartOut
from app.services.cart_service import cartService

router = APIRouter(tags=["cart"])

# Registration always creates the "buyer" role (routers/auth.py), so carts are for buyers.
buyer_only = require_role("buyer")


@router.get("/cart", response_model=CartOut)
def get_cart(db: Session = Depends(get_db), user: dict = Depends(buyer_only)):
    return cartService.get_or_create_cart(db, user_id=user["id"])


@router.post("/cart/items", response_model=CartOut)
def add_to_cart(
    payload: CartItemAdd,
    db: Session = Depends(get_db),
    user: dict = Depends(buyer_only),
):
    # raises PRODUCT_NOT_FOUND / VARIANT_NOT_FOUND / VARIANT_REQUIRED /
    # OUT_OF_STOCK / INSUFFICIENT_STOCK / QUANTITY_LIMIT
    return cartService.add_item(db, user_id=user["id"], payload=payload.model_dump())


@router.patch("/cart/items/{item_id}", response_model=CartOut)
def update_cart_item(
    item_id: UUID,
    payload: CartItemUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(buyer_only),
):
    cart = cartService.update_item(
        db, user_id=user["id"], item_id=item_id, quantity=payload.quantity
    )
    if not cart:
        raise api_error(404, "CART_ITEM_NOT_FOUND", "Cart item not found.")
    return cart


@router.delete("/cart/items/{item_id}", status_code=204)
def remove_cart_item(
    item_id: UUID,
    db: Session = Depends(get_db),
    user: dict = Depends(buyer_only),
):
    if not cartService.remove_item(db, user_id=user["id"], item_id=item_id):
        raise api_error(404, "CART_ITEM_NOT_FOUND", "Cart item not found.")


@router.delete("/cart", status_code=204)
def clear_cart(db: Session = Depends(get_db), user: dict = Depends(buyer_only)):
    cartService.clear_cart(db, user_id=user["id"])