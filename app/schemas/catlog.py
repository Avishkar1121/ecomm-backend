from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


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
    status: str = "draft"


class ProductUpdate(BaseModel):
    seller_id: UUID | None = None
    category_id: UUID | None = None
    brand_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=250)
    slug: str | None = Field(default=None, min_length=1, max_length=280)
    description: str | None = None
    status: str | None = None
