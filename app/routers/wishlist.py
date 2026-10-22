from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.deps import require_role
from app.schemas.wishlist import Page, WishlistItemAdd, WishlistItemOut
from app.services.wishlist_service import wishlistService

router = APIRouter(tags=["wishlist"])


@router.get("/wishlist", response_model=Page[WishlistItemOut])
def get_wishlist(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("buyer")),
):
    return Page[WishlistItemOut](
        **wishlistService.list_items(db, user_id=user["id"], page=page, page_size=page_size)
    )


@router.post("/wishlist", response_model=WishlistItemOut, status_code=201)
def add_to_wishlist(
    payload: WishlistItemAdd,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("buyer")),
):
    item = wishlistService.add_item(db, user_id=user["id"], product_id=payload.product_id)
    if not item:
        raise HTTPException(status_code=400, detail="Could not add item to wishlist")
    return item


@router.delete("/wishlist/{product_id}", status_code=204)
def remove_from_wishlist(
    product_id: UUID,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("buyer")),
):
    removed = wishlistService.remove_item(db, user_id=user["id"], product_id=product_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Wishlist item not found")


@router.get("/wishlist/{product_id}/check", response_model=dict)
def check_wishlist(
    product_id: UUID,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("buyer")),
):
    saved = wishlistService.is_saved(db, user_id=user["id"], product_id=product_id)
    return {"saved": saved}


@router.delete("/wishlist", status_code=204)
def clear_wishlist(
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("buyer")),
):
    wishlistService.clear(db, user_id=user["id"])