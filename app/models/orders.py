from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, CheckConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UserDefinedType

from .base import Base


class OrderStatusType(UserDefinedType):
    """Use the existing PostgreSQL order_status enum without hard-coding its labels."""
    cache_ok = True
    render_bind_cast = True

    def get_col_spec(self, **kw):
        return "order_status"

    def bind_processor(self, dialect):
        return lambda value: value if value is None else str(value)

    def result_processor(self, dialect, coltype):
        return lambda value: value if value is None else str(value)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("total = ((subtotal + shipping_fee) - discount)", name="orders_check"),
        CheckConstraint("discount >= 0", name="orders_discount_check"),
        CheckConstraint("shipping_fee >= 0", name="orders_shipping_fee_check"),
        CheckConstraint("subtotal >= 0", name="orders_subtotal_check"),
        CheckConstraint("total >= 0", name="orders_total_check"),
        UniqueConstraint("order_number", name="orders_order_number_key"),
        Index("idx_orders_status", "status"),
        Index("idx_orders_user", "user_id", "placed_at"),
        Index("uq_orders_idempotency", "user_id", "idempotency_key", unique=True),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    order_number: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("(('MH'::text || to_char(now(), 'YYMM'::text)) || lpad((nextval('order_number_seq'::regclass))::text, 6, '0'::text))"))
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(OrderStatusType(), nullable=False, server_default=text("'pending'"))
    shipping_address: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    shipping_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0"))
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    estimated_delivery_date: Mapped[date | None] = mapped_column(Date)
    coupon_code: Mapped[str | None] = mapped_column(String(40))
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.id")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("line_total = (unit_price * quantity::numeric)", name="order_items_check"),
        CheckConstraint("line_total >= 0", name="order_items_line_total_check"),
        CheckConstraint("quantity > 0", name="order_items_quantity_check"),
        CheckConstraint("unit_price >= 0", name="order_items_unit_price_check"),
        Index("idx_order_items_order", "order_id"),
        Index("idx_order_items_seller", "seller_id", "status"),
        Index("idx_order_items_variant", "variant_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    order_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    seller_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("seller_profiles.user_id", ondelete="RESTRICT"), nullable=False)
    variant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=False)
    product_title: Mapped[str] = mapped_column(String(250), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(OrderStatusType(), nullable=False, server_default=text("'pending'"))
    order: Mapped[Order] = relationship(back_populates="items")


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"
    __table_args__ = (Index("idx_order_history_order", "order_id", "changed_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(OrderStatusType(), nullable=False)
    changed_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
