from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import api_error
from app.models.cart_wishlist import Cart, CartItem
from app.models.catalog import Product, ProductVariant, SellerProfile
from app.models.orders import Order, OrderItem, OrderStatusHistory

_ZERO = Decimal("0.00")


def _order_query():
    return select(Order).options(selectinload(Order.items))


class OrderService:
    def place_order(self, db: Session, user_id: UUID, shipping_address: dict, idempotency_key: str | None = None) -> Order:
        if idempotency_key:
            prior = db.scalar(_order_query().where(Order.user_id == user_id, Order.idempotency_key == idempotency_key))
            if prior:
                return prior

        try:
            # Serialize checkout attempts for this customer's cart.
            cart = db.scalar(select(Cart).where(Cart.user_id == user_id).with_for_update())
            if not cart:
                raise api_error(400, "CART_EMPTY", "Your cart is empty.")

            cart_items = db.scalars(
                select(CartItem).where(CartItem.cart_id == cart.id).order_by(CartItem.variant_id, CartItem.id)
            ).all()
            if not cart_items:
                raise api_error(400, "CART_EMPTY", "Your cart is empty.")

            quantities: dict[UUID, int] = {}
            for item in cart_items:
                quantities[item.variant_id] = quantities.get(item.variant_id, 0) + item.quantity

            # Lock variants in deterministic order to avoid competing checkout deadlocks.
            variants = db.scalars(
                select(ProductVariant)
                .join(Product, Product.id == ProductVariant.product_id)
                .join(SellerProfile, SellerProfile.user_id == Product.seller_id)
                .where(ProductVariant.id.in_(list(quantities)))
                .order_by(ProductVariant.id)
                .with_for_update(of=ProductVariant)
            ).all()
            variant_by_id = {v.id: v for v in variants}
            if len(variant_by_id) != len(quantities):
                raise api_error(409, "PRODUCT_UNAVAILABLE", "One or more cart items are no longer available.")

            subtotal = _ZERO
            item_payloads = []
            for variant_id, quantity in quantities.items():
                variant = variant_by_id[variant_id]
                product = db.scalar(
                    select(Product).where(Product.id == variant.product_id)
                )
                seller = db.get(SellerProfile, product.seller_id) if product else None
                if (not product or product.status != "active" or product.deleted_at is not None
                        or not variant.is_active or not seller or seller.verification_status != "approved"):
                    raise api_error(409, "PRODUCT_UNAVAILABLE", "One or more cart items are no longer available.")
                if quantity > variant.stock_qty:
                    raise api_error(409, "INSUFFICIENT_STOCK", f"Insufficient stock for {product.title}. Available: {variant.stock_qty}.")
                if quantity <= 0:
                    raise api_error(409, "INVALID_QUANTITY", "Cart contains an invalid quantity.")
                price = Decimal(variant.price).quantize(Decimal("0.01"))
                line_total = (price * quantity).quantize(Decimal("0.01"))
                subtotal += line_total
                item_payloads.append({
                    "seller_id": product.seller_id,
                    "variant_id": variant.id,
                    "product_title": product.title,
                    "sku": variant.sku,
                    "unit_price": price,
                    "quantity": quantity,
                    "line_total": line_total,
                    "variant": variant,
                })

            shipping_fee = _ZERO
            discount = _ZERO  # Coupons are not applied until coupon validation is implemented.
            order = Order(
                user_id=user_id,
                status="pending",
                shipping_address=shipping_address,
                subtotal=subtotal,
                shipping_fee=shipping_fee,
                discount=discount,
                total=subtotal + shipping_fee - discount,
                idempotency_key=idempotency_key,
            )
            db.add(order)
            db.flush()  # obtain the generated order UUID and order_number

            for payload in item_payloads:
                variant = payload.pop("variant")
                db.add(OrderItem(order_id=order.id, status="pending", **payload))
                variant.stock_qty -= payload["quantity"]

            db.add(OrderStatusHistory(order_id=order.id, status="pending", changed_by=user_id, note="Order placed"))
            db.execute(delete(CartItem).where(CartItem.cart_id == cart.id))
            db.commit()
            return db.scalar(_order_query().where(Order.id == order.id))  # fresh, eagerly loaded response

        except IntegrityError:
            db.rollback()
            # A concurrent retry with the same idempotency key may have won the unique-index race.
            if idempotency_key:
                prior = db.scalar(_order_query().where(Order.user_id == user_id, Order.idempotency_key == idempotency_key))
                if prior:
                    return prior
            raise api_error(409, "ORDER_CONFLICT", "The order could not be placed due to a conflicting request. Please retry.")
        except Exception:
            db.rollback()
            raise

    def list_orders(self, db: Session, user_id: UUID, limit: int = 20, offset: int = 0) -> list[Order]:
        return db.scalars(
            _order_query().where(Order.user_id == user_id).order_by(Order.placed_at.desc()).limit(limit).offset(offset)
        ).all()

    def get_order(self, db: Session, user_id: UUID, order_id: UUID) -> Order:
        order = db.scalar(_order_query().where(Order.user_id == user_id, Order.id == order_id))
        if not order:
            raise api_error(404, "ORDER_NOT_FOUND", "Order not found.")
        return order


orderService = OrderService()
