from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import api_error
from app.models.cart_wishlist import Cart, CartItem
from app.models.catalog import Product, ProductVariant, SellerProfile
from app.repositories.cart_repo import cartRepository
from app.schemas.cart import MAX_LINE_QTY

_CENT = Decimal("0.01")


def _money(value: Decimal) -> float:
    """Money is computed in Decimal and only converted to float at the API boundary."""
    return float(Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP))


class CartService:

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sellable_product(db: Session, product_id: uuid.UUID) -> Product:
        """Active, not deleted, and the seller is approved."""
        product = db.scalar(
            select(Product)
            .join(SellerProfile, SellerProfile.user_id == Product.seller_id)
            .where(
                Product.id == product_id,
                Product.status == "active",
                Product.deleted_at.is_(None),
                SellerProfile.verification_status == "approved",
            )
        )
        if not product:
            raise api_error(404, "PRODUCT_NOT_FOUND", "This product is not available.")
        return product

    @staticmethod
    def _resolve_variant(
        db: Session, product: Product, variant_id: uuid.UUID | None
    ) -> ProductVariant:
        """Always returns a concrete active variant that belongs to the product."""
        if variant_id is not None:
            variant = db.scalar(
                select(ProductVariant).where(
                    ProductVariant.id == variant_id,
                    ProductVariant.product_id == product.id,
                    ProductVariant.is_active.is_(True),
                )
            )
            if not variant:
                raise api_error(404, "VARIANT_NOT_FOUND", "This variant is not available.")
            return variant

        variants = db.scalars(
            select(ProductVariant)
            .where(ProductVariant.product_id == product.id, ProductVariant.is_active.is_(True))
            .order_by(ProductVariant.price, ProductVariant.id)
            .limit(2)
        ).all()
        if not variants:
            raise api_error(409, "PRODUCT_UNAVAILABLE", "This product is not available.")
        if len(variants) > 1:
            raise api_error(400, "VARIANT_REQUIRED", "Please choose a variant.")
        return variants[0]

    @staticmethod
    def _check_qty(variant: ProductVariant, qty: int) -> None:
        if qty > MAX_LINE_QTY:
            raise api_error(400, "QUANTITY_LIMIT", f"You can add at most {MAX_LINE_QTY} of an item.")
        if variant.stock_qty <= 0:
            raise api_error(409, "OUT_OF_STOCK", "This item is out of stock.")
        if qty > variant.stock_qty:
            raise api_error(409, "INSUFFICIENT_STOCK", f"Only {variant.stock_qty} left in stock.")

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    @staticmethod
    def _line(item: CartItem) -> dict[str, Any]:
        variant = item.variant
        product = variant.product if variant else None
        seller = product.seller if product else None
        is_available = bool(
            product and variant and seller
            and product.status == "active"
            and product.deleted_at is None
            and variant.is_active
            and seller.verification_status == "approved"
        )
        stock = int(variant.stock_qty) if variant else 0
        live_price = Decimal(variant.price) if variant else Decimal(0)

        issue = None
        if not is_available:
            issue = "UNAVAILABLE"
        elif stock <= 0:
            issue = "OUT_OF_STOCK"
        elif item.quantity > stock:
            issue = "INSUFFICIENT_STOCK"

        images = product.images if product else []
        return {
            "id": item.id,
            "product_id": product.id if product else None,
            "variant_id": item.variant_id,
            "quantity": item.quantity,
            "unit_price": _money(live_price),
            "in_stock": is_available and stock > 0,
            "available_qty": stock if is_available else 0,
            "is_available": is_available,
            "issue": issue,
            "line_total": _money(live_price * item.quantity),
            "product_title": product.title if product else None,
            "product_slug": product.slug if product else None,
            "image_url": images[0].url if images else None,
            "variant_name": variant.name if variant else None,
            "added_at": item.added_at,
            "_live_price": live_price,
        }

    def _serialize(self, cart: Cart) -> dict[str, Any]:
        lines = [self._line(i) for i in cart.items]
        buyable = [l for l in lines if l["is_available"]]
        subtotal = sum((l["_live_price"] * l["quantity"] for l in buyable), Decimal("0"))
        discount = Decimal("0")   # TODO: apply coupon discount when cart.coupon_id is set
        for l in lines:
            l.pop("_live_price")
        return {
            "id": cart.id,
            "user_id": cart.user_id,
            "coupon_id": cart.coupon_id,
            "items": lines,
            "item_count": sum(l["quantity"] for l in buyable),
            "has_issues": any(l["issue"] for l in lines),
            "subtotal": _money(subtotal),
            "discount": _money(discount),
            "total": _money(subtotal - discount),
            "created_at": cart.created_at,
            "updated_at": cart.updated_at,
        }

    # ------------------------------------------------------------------
    # Cart access
    # ------------------------------------------------------------------

    def _get_or_create(self, db: Session, user_id: uuid.UUID) -> Cart:
        cart = cartRepository.get_by_user(db, user_id)
        if cart:
            return cart
        try:
            cartRepository.create(db, user_id)
            db.commit()
        except IntegrityError:
            # two first requests raced; carts.user_id is UNIQUE, so just read the winner
            db.rollback()
        return cartRepository.get_by_user(db, user_id)  # type: ignore[return-value]

    def _reload(self, db: Session, user_id: uuid.UUID) -> Cart:
        """Commit (also releases the cart lock) then re-fetch with fresh eager loads."""
        db.commit()
        return cartRepository.get_by_user(db, user_id)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_create_cart(self, db: Session, user_id: uuid.UUID) -> dict[str, Any]:
        return self._serialize(self._get_or_create(db, user_id))

    def add_item(self, db: Session, user_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
        cart = self._get_or_create(db, user_id)
        cartRepository.lock_cart(db, cart.id)     # concurrent adds queue up here

        product = self._sellable_product(db, payload["product_id"])
        variant = self._resolve_variant(db, product, payload.get("variant_id"))

        existing = cartRepository.get_item_by_variant(db, cart.id, variant.id)
        new_qty = (existing.quantity if existing else 0) + payload.get("quantity", 1)
        self._check_qty(variant, new_qty)

        if existing:
            existing.quantity = new_qty
        else:
            cartRepository.add_item(db, cart.id, variant.id, new_qty)
        cartRepository.touch(db, cart.id)

        return self._serialize(self._reload(db, user_id))

    def update_item(
        self, db: Session, user_id: uuid.UUID, item_id: uuid.UUID, quantity: int
    ) -> dict[str, Any] | None:
        cart = cartRepository.get_by_user(db, user_id)
        if not cart:
            return None
        cartRepository.lock_cart(db, cart.id)

        item = cartRepository.get_item(db, cart.id, item_id)
        if not item:
            return None

        if quantity == 0:
            db.delete(item)
        else:
            # Decreasing is always allowed (lets the user fix an over-stock cart);
            # only an increase has to pass the availability and stock checks.
            if quantity > item.quantity:
                variant = db.get(ProductVariant, item.variant_id)
                if not variant or not variant.is_active:
                    raise api_error(409, "PRODUCT_UNAVAILABLE", "This item is no longer available.")
                self._sellable_product(db, variant.product_id)
                self._check_qty(variant, quantity)
            item.quantity = quantity
        cartRepository.touch(db, cart.id)

        return self._serialize(self._reload(db, user_id))

    def remove_item(self, db: Session, user_id: uuid.UUID, item_id: uuid.UUID) -> bool:
        cart = cartRepository.get_by_user(db, user_id)
        if not cart:
            return False
        removed = cartRepository.delete_item(db, cart.id, item_id)
        if removed:
            cartRepository.touch(db, cart.id)
        db.commit()
        return removed

    def clear_cart(self, db: Session, user_id: uuid.UUID) -> None:
        cart = cartRepository.get_by_user(db, user_id)
        if cart:
            cartRepository.clear_items(db, cart.id)
            cartRepository.touch(db, cart.id)
            db.commit()


cartService = CartService()