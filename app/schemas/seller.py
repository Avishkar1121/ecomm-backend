from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator, HttpUrl

class SellerProfileCreate(BaseModel):
    store_name: str = Field(..., min_length=2, max_length=150)
    store_slug: str = Field(..., min_length=2, max_length=160, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str | None = None
    logo_url: str | None = Field(default=None, max_length=2048)
    gstin: str | None = Field(default=None, min_length=15, max_length=15)
    @field_validator("gstin")
    @classmethod
    def normalize_gstin(cls, value): return value.upper() if value else value

class SellerProfileUpdate(BaseModel):
    store_name: str | None = Field(default=None, min_length=2, max_length=150)
    store_slug: str | None = Field(default=None, min_length=2, max_length=160, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str | None = None
    logo_url: str | None = Field(default=None, max_length=2048)
    gstin: str | None = Field(default=None, min_length=15, max_length=15)
    @field_validator("gstin")
    @classmethod
    def normalize_gstin(cls, value): return value.upper() if value else value

class SellerProfileOut(BaseModel):
    user_id: UUID; store_name: str; store_slug: str; description: str | None = None
    logo_url: str | None = None; gstin: str | None = None
    verification_status: Literal["pending", "approved", "rejected", "suspended"]
    commission_rate: Decimal; created_at: datetime; updated_at: datetime

class SellerVariantCreate(BaseModel):
    sku: str = Field(..., min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    mrp: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    price: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    stock_qty: int = Field(default=0, ge=0)
    is_active: bool = True
    @model_validator(mode="after")
    def price_not_above_mrp(self):
        if self.price > self.mrp: raise ValueError("Variant selling price cannot exceed MRP")
        return self

class SellerVariantUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    mrp: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    price: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    stock_qty: int | None = Field(default=None, ge=0)
    is_active: bool | None = None
    @model_validator(mode="after")
    def validate_price(self):
        if "mrp" in self.model_fields_set and self.mrp is None: raise ValueError("mrp cannot be null")
        if "price" in self.model_fields_set and self.price is None: raise ValueError("price cannot be null")
        if self.mrp is not None and self.price is not None and self.price > self.mrp: raise ValueError("Variant selling price cannot exceed MRP")
        return self

class SellerVariantOut(BaseModel):
    id: UUID; product_id: UUID; sku: str; name: str | None = None
    mrp: Decimal; price: Decimal; stock_qty: int; is_active: bool
    created_at: datetime; updated_at: datetime

class SellerImageCreate(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    alt_text: str | None = Field(default=None, max_length=200)
    sort_order: int = Field(default=0, ge=0)
    is_primary: bool = False

class SellerImageOut(BaseModel):
    id: UUID; product_id: UUID; url: str; alt_text: str | None = None
    sort_order: int; is_primary: bool

class SellerProductCreate(BaseModel):
    category_id: UUID; brand_id: UUID | None = None
    title: str = Field(..., min_length=1, max_length=250)
    slug: str = Field(..., min_length=1, max_length=280, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str | None = None
    status: Literal["draft", "active", "archived"] = "draft"
    variants: list[SellerVariantCreate] = Field(default_factory=list, max_length=100)
    @model_validator(mode="after")
    def active_product_requires_variant(self):
        if self.status == "active" and not self.variants: raise ValueError("At least one variant is required to create an active product")
        skus=[v.sku for v in self.variants]
        if len(skus)!=len(set(skus)): raise ValueError("Variant SKUs must be unique within the product")
        return self

class SellerProductUpdate(BaseModel):
    category_id: UUID | None = None; brand_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=250)
    slug: str | None = Field(default=None, min_length=1, max_length=280, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str | None = None; status: Literal["draft", "active", "archived"] | None = None
    @model_validator(mode="after")
    def required_columns_cannot_be_cleared(self):
        for f in ("category_id", "title", "slug", "status"):
            if f in self.model_fields_set and getattr(self,f) is None: raise ValueError(f"{f} cannot be null")
        return self

class SellerProductOut(BaseModel):
    id: UUID; seller_id: UUID; category_id: UUID; category_name: str
    brand_id: UUID | None = None; brand_name: str | None = None
    title: str; slug: str; description: str | None = None
    status: Literal["draft", "active", "archived"]
    avg_rating: Decimal; review_count: int; variants_count: int; total_stock: int
    in_stock: bool; primary_image_url: str | None = None
    created_at: datetime; updated_at: datetime

class SellerProductPage(BaseModel):
    items: list[SellerProductOut]; total: int; page: int; page_size: int
