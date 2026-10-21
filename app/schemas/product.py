from datetime import datetime
from decimal import Decimal
from typing import Generic, TypeVar
from uuid import UUID
from pydantic import BaseModel, ConfigDict
T = TypeVar("T")
class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
class CategoryOut(BaseModel):
    id: UUID; parent_id: UUID | None; name: str; slug: str
class BrandOut(BaseModel):
    id: UUID; name: str; slug: str; logo_url: str | None
class ImageOut(BaseModel):
    url: str; alt_text: str | None; is_primary: bool
class VariantOut(BaseModel):
    id: UUID; sku: str; name: str | None; price: Decimal; mrp: Decimal; stock_qty: int; in_stock: bool
class ProductCard(BaseModel):
    id: UUID; slug: str; title: str; category: str; category_slug: str; brand: str | None
    image_url: str | None; price: Decimal; mrp: Decimal; discount_percent: int
    avg_rating: Decimal; review_count: int; in_stock: bool
class ProductDetail(ProductCard):
    description: str | None; seller_store: str; seller_slug: str; images: list[ImageOut]; variants: list[VariantOut]
