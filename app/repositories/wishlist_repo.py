from __future__ import annotations

import math
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.cart_wishlist import WishlistItem


class WishlistRepository:

    def get_by_user_and_product(
        self, db: Session, user_id: uuid.UUID, product_id: uuid.UUID
    ) -> WishlistItem | None:
        return db.scalar(
            select(WishlistItem).where(
                WishlistItem.user_id == user_id,
                WishlistItem.product_id == product_id,
            )
        )

    def exists(self, db: Session, user_id: uuid.UUID, product_id: uuid.UUID) -> bool:
        return (
            db.scalar(
                select(WishlistItem.id).where(
                    WishlistItem.user_id == user_id,
                    WishlistItem.product_id == product_id,
                )
            )
            is not None
        )

    def list_paginated(
        self,
        db: Session,
        user_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[WishlistItem], int]:
        total: int = db.scalar(
            select(func.count()).where(WishlistItem.user_id == user_id)
        ) or 0
        rows = db.scalars(
            select(WishlistItem)
            .where(WishlistItem.user_id == user_id)
            .order_by(WishlistItem.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return list(rows), total

    def create(self, db: Session, user_id: uuid.UUID, product_id: uuid.UUID) -> WishlistItem:
        item = WishlistItem(user_id=user_id, product_id=product_id)
        db.add(item)
        db.flush()
        db.refresh(item)
        return item

    def delete(self, db: Session, user_id: uuid.UUID, product_id: uuid.UUID) -> bool:
        from sqlalchemy import delete as sa_delete

        result = db.execute(
            sa_delete(WishlistItem).where(
                WishlistItem.user_id == user_id,
                WishlistItem.product_id == product_id,
            )
        )
        return result.rowcount > 0

    def clear(self, db: Session, user_id: uuid.UUID) -> None:
        from sqlalchemy import delete as sa_delete

        db.execute(sa_delete(WishlistItem).where(WishlistItem.user_id == user_id))


wishlistRepository = WishlistRepository()