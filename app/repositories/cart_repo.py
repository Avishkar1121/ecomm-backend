from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from app.models.cart_wishlist import Cart, CartItem
from app.models.catalog import Product, ProductVariant


def _with_items():
    """Eager-load options that prevent N+1 when serializing cart items."""
    return selectinload(Cart.items).options(
        selectinload(CartItem.variant).options(
            selectinload(ProductVariant.product).options(
                selectinload(Product.images),
                selectinload(Product.seller),
            ),
        ),
    )


class CartRepository:

    def get_by_user(self, db: Session, user_id: uuid.UUID) -> Cart | None:
        return db.scalar(
            select(Cart)
            .where(Cart.user_id == user_id)
            .options(_with_items())
            # items may have changed in this session; always read fresh rows
            .execution_options(populate_existing=True)
        )

    def create(self, db: Session, user_id: uuid.UUID) -> Cart:
        cart = Cart(user_id=user_id)
        db.add(cart)
        db.flush()          # raises IntegrityError if another request created it first
        return cart

    def lock_cart(self, db: Session, cart_id: uuid.UUID) -> None:
        """Row lock held until commit/rollback: serialises concurrent writes to one cart."""
        db.execute(select(Cart.id).where(Cart.id == cart_id).with_for_update())

    def touch(self, db: Session, cart_id: uuid.UUID) -> None:
        """Items live in another table, so the cart row's updated_at must be bumped by hand."""
        db.execute(update(Cart).where(Cart.id == cart_id).values(updated_at=func.now()))

    def get_item(self, db: Session, cart_id: uuid.UUID, item_id: uuid.UUID) -> CartItem | None:
        return db.scalar(
            select(CartItem).where(
                CartItem.id == item_id,
                CartItem.cart_id == cart_id,
            ).execution_options(populate_existing=True)   # re-read AFTER the cart lock
        )

    def get_item_by_variant(
        self, db: Session, cart_id: uuid.UUID, variant_id: uuid.UUID
    ) -> CartItem | None:
        return db.scalar(
            select(CartItem).where(
                CartItem.cart_id == cart_id,
                CartItem.variant_id == variant_id,
            ).execution_options(populate_existing=True)   # re-read AFTER the cart lock
        )

    def add_item(
        self, db: Session, cart_id: uuid.UUID, variant_id: uuid.UUID, quantity: int
    ) -> CartItem:
        item = CartItem(cart_id=cart_id, variant_id=variant_id, quantity=quantity)
        db.add(item)
        return item

    def delete_item(self, db: Session, cart_id: uuid.UUID, item_id: uuid.UUID) -> bool:
        result = db.execute(
            delete(CartItem).where(
                CartItem.id == item_id,
                CartItem.cart_id == cart_id,
            )
        )
        return result.rowcount > 0

    def clear_items(self, db: Session, cart_id: uuid.UUID) -> None:
        db.execute(delete(CartItem).where(CartItem.cart_id == cart_id))


cartRepository = CartRepository()