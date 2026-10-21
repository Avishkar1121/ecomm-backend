from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.deps import require_role
from app.schemas.catlog import (
    BrandCreate,
    BrandOut,
    BrandUpdate,
    CategoryCreate,
    CategoryOut,
    CategoryUpdate,
    Page,
    ProductCard,
    ProductAdminOut,
    ProductCreate,
    ProductDetail,
    ProductUpdate,
    RatingBreakdown,
    ReviewOut,
)
from app.services.catlog_service import catlogService

router = APIRouter(tags=["catlog"])


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    return catlogService.list_categories(db)


@router.get("/brands", response_model=list[BrandOut])
def list_brands(
    category: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return catlogService.list_brands(db, category=category)


@router.get("/products", response_model=Page[ProductCard])
def list_products(
    q: str | None = Query(default=None, max_length=100),
    category: str | None = Query(default=None, max_length=120),
    brand: list[str] = Query(default=[]),
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
    min_rating: float | None = Query(default=None, ge=0, le=5),
    in_stock: bool = False,
    sort: Literal["newest", "price_asc", "price_desc", "rating", "trending"] = "newest",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=48),
    db: Session = Depends(get_db),
):
    if max_price is not None and min_price is not None and max_price < min_price:
        raise HTTPException(status_code=422, detail="max_price must be >= min_price")

    data = catlogService.list_products(
        db=db,
        q=q,
        category=category,
        brand=brand,
        min_price=min_price,
        max_price=max_price,
        min_rating=min_rating,
        in_stock=in_stock,
        sort=sort,
        page=page,
        page_size=page_size,
    )

    return Page[ProductCard](**data)


@router.get("/products/{slug}", response_model=ProductDetail)
def product_detail(slug: str, db: Session = Depends(get_db)):
    return catlogService.get_product_detail(db, slug)


@router.get("/products/{slug}/rating-summary", response_model=RatingBreakdown)
def rating_summary(slug: str, db: Session = Depends(get_db)):
    return catlogService.get_rating_summary(db, slug)


@router.get("/products/{slug}/reviews", response_model=Page[ReviewOut])
def product_reviews(
    slug: str,
    rating: int | None = Query(default=None, ge=1, le=5),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return Page[ReviewOut](**catlogService.get_product_reviews(db, slug, rating, page, page_size))


@router.get("/products/{slug}/related", response_model=list[ProductCard])
def related_products(
    slug: str,
    limit: int = Query(default=8, ge=1, le=24),
    db: Session = Depends(get_db),
):
    return catlogService.get_related_products(db, slug, limit)


@router.post("/admin/categories", response_model=CategoryOut)
def create_category(
    payload: CategoryCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    return catlogService.create_category(db, payload.model_dump())


@router.put("/admin/categories/{category_id}", response_model=CategoryOut)
def update_category(
    category_id: UUID,
    payload: CategoryUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    row = catlogService.update_category(db, category_id, payload.model_dump(exclude_unset=True))
    if not row:
        raise HTTPException(status_code=404, detail="Category not found")
    return row


@router.delete("/admin/categories/{category_id}", status_code=204)
def delete_category(
    category_id: UUID,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    deleted = catlogService.delete_category(db, category_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Category not found")


@router.post("/admin/brands", response_model=BrandOut)
def create_brand(
    payload: BrandCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    return catlogService.create_brand(db, payload.model_dump())


@router.put("/admin/brands/{brand_id}", response_model=BrandOut)
def update_brand(
    brand_id: UUID,
    payload: BrandUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    row = catlogService.update_brand(db, brand_id, payload.model_dump(exclude_unset=True))
    if not row:
        raise HTTPException(status_code=404, detail="Brand not found")
    return row


@router.delete("/admin/brands/{brand_id}", status_code=204)
def delete_brand(
    brand_id: UUID,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    deleted = catlogService.delete_brand(db, brand_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Brand not found")


@router.post("/admin/products", response_model=ProductAdminOut)
def create_product(
    payload: ProductCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    row = catlogService.create_product(db, payload.model_dump())
    if not row:
        raise HTTPException(status_code=400, detail="Create failed")
    return row


@router.put("/admin/products/{product_id}", response_model=ProductAdminOut)
def update_product(
    product_id: UUID,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    row = catlogService.update_product(db, product_id, payload.model_dump(exclude_unset=True))
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    return row


@router.delete("/admin/products/{product_id}", status_code=204)
def delete_product(
    product_id: UUID,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    deleted = catlogService.delete_product(db, product_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Product not found")