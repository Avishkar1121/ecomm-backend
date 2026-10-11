from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class CategoryOut(BaseModel):
    id: UUID
    parent_id: UUID | None
    name: str
    slug: str


class BrandOut(BaseModel):
    id: UUID
    name: str
    slug: str
    logo_url: str | None


class ProductCard(BaseModel):
    id: UUID
    slug: str
    title: str
    category: str
    category_slug: str
    brand: str | None
    image_url: str | None
    price: float
    mrp: float
    discount_percent: int
    avg_rating: float
    review_count: int
    in_stock: bool


class ImageOut(BaseModel):
    url: str
    alt_text: str | None
    is_primary: bool


class VariantOut(BaseModel):
    id: UUID
    sku: str
    name: str | None
    price: float
    mrp: float
    stock_qty: int
    in_stock: bool


class RatingBreakdown(BaseModel):
    total_reviews: int = 0
    avg_rating: float = 0
    five_star: int = 0
    four_star: int = 0
    three_star: int = 0
    two_star: int = 0
    one_star: int = 0


class ProductDetail(BaseModel):
    id: UUID
    slug: str
    title: str
    description: str | None
    category: str
    category_slug: str
    brand: str | None
    seller_store: str
    seller_slug: str
    features: list[str]
    specs: dict
    images: list[ImageOut]
    variants: list[VariantOut]
    price: float
    mrp: float
    discount_percent: int
    in_stock: bool
    rating: RatingBreakdown


class ReviewOut(BaseModel):
    id: UUID
    rating: int
    title: str | None
    body: str | None
    reviewer: str
    created_at: datetime