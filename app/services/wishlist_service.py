from __future__ import annotations

import math
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.cart_wishlist import WishlistItem
from app.repositories.wishlist_repo import wishlistRepository


class WishlistService:

    def _serialize(self, item: WishlistItem) -> dict[str, Any]:
        p = item.product
        # Price and stock live on ProductVariant; use the cheapest active variant
        cheapest = None
        if p and p.variants:
            active = [v for v in p.variants if v.is_active]
            if active:
                cheapest = min(active, key=lambda v: v.price)
        return {
            "id": item.id,
            "product_id": item.product_id,
            "product_title": p.title if p else None,
            "product_slug": p.slug if p else None,
            "image_url": (
                p.images[0].url if p and p.images else None
            ),
            "price": float(cheapest.price) if cheapest else None,
            "in_stock": (cheapest.stock_qty > 0) if cheapest else None,
            "created_at": item.created_at,
        }

    def list_items(
        self,
        db: Session,
        user_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        rows, total = wishlistRepository.list_paginated(db, user_id, page, page_size)
        return {
            "items": [self._serialize(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, math.ceil(total / page_size)),
        }

    def add_item(
        self, db: Session, user_id: uuid.UUID, product_id: uuid.UUID
    ) -> dict[str, Any] | None:
        existing = wishlistRepository.get_by_user_and_product(db, user_id, product_id)
        if existing:
            return self._serialize(existing)

        item = wishlistRepository.create(db, user_id, product_id)
        db.commit()
        db.refresh(item)
        return self._serialize(item)

    def remove_item(
        self, db: Session, user_id: uuid.UUID, product_id: uuid.UUID
    ) -> bool:
        removed = wishlistRepository.delete(db, user_id, product_id)
        db.commit()
        return removed

    def is_saved(
        self, db: Session, user_id: uuid.UUID, product_id: uuid.UUID
    ) -> bool:
        return wishlistRepository.exists(db, user_id, product_id)

    def clear(self, db: Session, user_id: uuid.UUID) -> None:
        wishlistRepository.clear(db, user_id)
        db.commit()


wishlistService = WishlistService()