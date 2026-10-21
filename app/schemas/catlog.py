from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

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
    logo_url: str | None = None


class ProductCard(BaseModel):
    id: UUID
    slug: str
    title: str
    category: str
    category_slug: str
    brand: str | None = None
    image_url: str | None = None
    price: float
    mrp: float
    discount_percent: int
    avg_rating: float
    review_count: int
    in_stock: bool


class ImageOut(BaseModel):
    url: str
    alt_text: str | None = None
    is_primary: bool = False


class VariantOut(BaseModel):
    id: UUID
    sku: str
    name: str | None = None
    price: float
    mrp: float
    stock_qty: int
    in_stock: bool


class RatingBreakdown(BaseModel):
    total_reviews: int = 0
    avg_rating: float = 0.0
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
    brand: str | None = None
    seller_store: str
    seller_slug: str
    features: list[str] = Field(default_factory=list)
    specs: dict = Field(default_factory=dict)
    images: list[ImageOut] = Field(default_factory=list)
    variants: list[VariantOut] = Field(default_factory=list)
    price: float
    mrp: float
    discount_percent: int
    in_stock: bool
    rating: RatingBreakdown


class ReviewOut(BaseModel):
    id: UUID
    rating: int
    title: str | None = None
    body: str | None = None
    reviewer: str
    created_at: datetime


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=120)
    parent_id: UUID | None = None
    sort_order: int = 0
    is_active: bool = True


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: UUID | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class BrandCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=120)
    logo_url: str | None = None


class BrandUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(default=None, min_length=1, max_length=120)
    logo_url: str | None = None


class ProductCreate(BaseModel):
    seller_id: UUID
    category_id: UUID
    brand_id: UUID | None = None
    title: str = Field(..., min_length=1, max_length=250)
    slug: str = Field(..., min_length=1, max_length=280)
    description: str | None = None
    status: Literal["draft", "active", "archived"] = "draft"


class ProductUpdate(BaseModel):
    seller_id: UUID | None = None
    category_id: UUID | None = None
    brand_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=250)
    slug: str | None = Field(default=None, min_length=1, max_length=280)
    description: str | None = None
    status: Literal["draft", "active", "archived"] | None = None

    @model_validator(mode="after")
    def required_columns_cannot_be_cleared(self):
        for field_name in ("seller_id", "category_id", "title", "slug", "status"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self

class ProductAdminOut(BaseModel):
    id: UUID
    seller_id: UUID
    category_id: UUID
    brand_id: UUID | None = None
    title: str
    slug: str
    description: str | None = None
    status: Literal["draft", "active", "archived"]
    avg_rating: float
    review_count: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
