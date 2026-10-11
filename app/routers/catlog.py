from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..schemas.catlog import (BrandOut, CategoryOut, ImageOut, Page, ProductCard, ProductDetail,
                               RatingBreakdown, ReviewOut, VariantOut)

router = APIRouter(tags=["catalog"])

# One query shape powers the home page, shop page, related products and search.
# The LATERAL subquery picks the lowest active variant price and drops products
# that have no active variant at all.
_LIST_SQL = """
SELECT p.id, p.slug, p.title,
       c.name AS category, c.slug AS category_slug, b.name AS brand,
       p.avg_rating, p.review_count,
       v.price, v.mrp, v.in_stock,
       img.url AS image_url,
       count(*) OVER () AS total_count
  FROM products p
  JOIN categories c ON c.id = p.category_id
  LEFT JOIN brands b ON b.id = p.brand_id
  JOIN LATERAL (
        SELECT min(pv.price) AS price,
               (array_agg(pv.mrp ORDER BY pv.price, pv.id))[1] AS mrp,
               bool_or(pv.stock_qty > 0) AS in_stock
          FROM product_variants pv
         WHERE pv.product_id = p.id AND pv.is_active
  ) v ON v.price IS NOT NULL
  LEFT JOIN LATERAL (
        SELECT pi.url FROM product_images pi
         WHERE pi.product_id = p.id
         ORDER BY pi.is_primary DESC, pi.sort_order LIMIT 1
  ) img ON true
 WHERE p.status = 'active' AND p.deleted_at IS NULL AND c.is_active
"""

# Sort options are looked up in this fixed map, so user input never reaches the SQL text.
_SORTS = {
    "newest":     "p.created_at DESC, p.id",
    "price_asc":  "v.price ASC, p.id",
    "price_desc": "v.price DESC, p.id",
    "rating":     "p.avg_rating DESC, p.review_count DESC, p.id",
    "trending":   "p.review_count DESC, p.avg_rating DESC, p.id",
}


def _discount(price: float, mrp: float) -> int:
    return round((mrp - price) / mrp * 100) if mrp and mrp > price else 0


def _card(row) -> ProductCard:
    price, mrp = float(row["price"]), float(row["mrp"])
    return ProductCard(
        id=row["id"], slug=row["slug"], title=row["title"], category=row["category"],
        category_slug=row["category_slug"], brand=row["brand"], image_url=row["image_url"],
        price=price, mrp=mrp, discount_percent=_discount(price, mrp),
        avg_rating=float(row["avg_rating"]), review_count=row["review_count"],
        in_stock=bool(row["in_stock"]))


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# ------------------------------------------------------------------ lookups
@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT id, parent_id, name, slug FROM categories
         WHERE is_active ORDER BY sort_order, name
    """)).mappings().all()
    return list(rows)


@router.get("/brands", response_model=list[BrandOut])
def list_brands(category: str | None = None, db: Session = Depends(get_db)):
    """Brands that have at least one active product (optionally within a category)."""
    rows = db.execute(text("""
        SELECT DISTINCT b.id, b.name, b.slug, b.logo_url
          FROM brands b
          JOIN products p ON p.brand_id = b.id AND p.status = 'active' AND p.deleted_at IS NULL
          JOIN categories c ON c.id = p.category_id
         WHERE (CAST(:cat AS text) IS NULL OR c.slug = :cat
                OR c.parent_id = (SELECT id FROM categories WHERE slug = :cat))
         ORDER BY b.name
    """), {"cat": category}).mappings().all()
    return list(rows)


# ------------------------------------------------------------------ product list
@router.get("/products", response_model=Page[ProductCard])
def list_products(
    q: str | None = Query(default=None, max_length=100),
    category: str | None = Query(default=None, max_length=120, description="category slug"),
    brand: list[str] = Query(default=[], description="brand slug, repeat for several"),
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
    min_rating: float | None = Query(default=None, ge=0, le=5),
    in_stock: bool = False,
    sort: Literal["newest", "price_asc", "price_desc", "rating", "trending"] = "newest",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=48),
    db: Session = Depends(get_db),
):
    sql, params = _LIST_SQL, {}

    if q and q.strip():
        sql += " AND (p.title ILIKE :q OR b.name ILIKE :q)"
        params["q"] = _like(q.strip())
    if category:
        # a parent category also matches products in its child categories
        sql += """ AND (c.slug = :cat OR c.parent_id = (SELECT id FROM categories WHERE slug = :cat))"""
        params["cat"] = category
    if brand:
        sql += " AND b.slug = ANY(:brands)"
        params["brands"] = brand
    if min_price is not None:
        sql += " AND v.price >= :min_price"
        params["min_price"] = min_price
    if max_price is not None:
        sql += " AND v.price <= :max_price"
        params["max_price"] = max_price
    if min_rating is not None:
        sql += " AND p.avg_rating >= :min_rating"
        params["min_rating"] = min_rating
    if in_stock:
        sql += " AND v.in_stock"

    sql += f" ORDER BY {_SORTS[sort]} LIMIT :limit OFFSET :offset"
    params.update(limit=page_size, offset=(page - 1) * page_size)

    rows = db.execute(text(sql), params).mappings().all()
    total = rows[0]["total_count"] if rows else 0
    return Page[ProductCard](items=[_card(r) for r in rows], total=total,
                             page=page, page_size=page_size)


# ------------------------------------------------------------------ product detail
def _get_product_or_404(db: Session, slug: str):
    row = db.execute(text("""
        SELECT p.id, p.slug, p.title, p.description, p.features, p.specs,
               p.category_id, c.name AS category, c.slug AS category_slug, b.name AS brand,
               sp.store_name AS seller_store, sp.store_slug AS seller_slug
          FROM products p
          JOIN categories c ON c.id = p.category_id
          LEFT JOIN brands b ON b.id = p.brand_id
          JOIN seller_profiles sp ON sp.user_id = p.seller_id
         WHERE p.slug = :slug AND p.status = 'active' AND p.deleted_at IS NULL
    """), {"slug": slug}).mappings().first()
    if not row:
        raise HTTPException(404, "Product not found")
    return row


def _rating(db: Session, product_id) -> RatingBreakdown:
    row = db.execute(text("SELECT * FROM v_rating_breakdown WHERE product_id = :id"),
                     {"id": product_id}).mappings().first()
    if not row:
        return RatingBreakdown()
    return RatingBreakdown(**{k: (float(v) if k == "avg_rating" else v)
                              for k, v in row.items() if k != "product_id"})


@router.get("/products/{slug}", response_model=ProductDetail)
def product_detail(slug: str, db: Session = Depends(get_db)):
    p = _get_product_or_404(db, slug)
    images = db.execute(text("""
        SELECT url, alt_text, is_primary FROM product_images
         WHERE product_id = :id ORDER BY is_primary DESC, sort_order
    """), {"id": p["id"]}).mappings().all()
    variants = db.execute(text("""
        SELECT id, sku, name, price, mrp, stock_qty FROM product_variants
         WHERE product_id = :id AND is_active ORDER BY price, name
    """), {"id": p["id"]}).mappings().all()
    if not variants:
        raise HTTPException(404, "Product not found")

    out_variants = [VariantOut(id=v["id"], sku=v["sku"], name=v["name"], price=float(v["price"]),
                               mrp=float(v["mrp"]), stock_qty=v["stock_qty"],
                               in_stock=v["stock_qty"] > 0) for v in variants]
    lowest = out_variants[0]
    return ProductDetail(
        id=p["id"], slug=p["slug"], title=p["title"], description=p["description"],
        category=p["category"], category_slug=p["category_slug"], brand=p["brand"],
        seller_store=p["seller_store"], seller_slug=p["seller_slug"],
        features=p["features"] or [], specs=p["specs"] or {},
        images=[ImageOut(**i) for i in images], variants=out_variants,
        price=lowest.price, mrp=lowest.mrp, discount_percent=_discount(lowest.price, lowest.mrp),
        in_stock=any(v.in_stock for v in out_variants), rating=_rating(db, p["id"]))


@router.get("/products/{slug}/rating-summary", response_model=RatingBreakdown)
def rating_summary(slug: str, db: Session = Depends(get_db)):
    return _rating(db, _get_product_or_404(db, slug)["id"])


@router.get("/products/{slug}/reviews", response_model=Page[ReviewOut])
def product_reviews(slug: str,
                    rating: int | None = Query(default=None, ge=1, le=5),
                    page: int = Query(default=1, ge=1),
                    page_size: int = Query(default=10, ge=1, le=50),
                    db: Session = Depends(get_db)):
    p = _get_product_or_404(db, slug)
    rows = db.execute(text("""
        SELECT r.id, r.rating, r.title, r.body, r.created_at,
               u.first_name || CASE WHEN u.last_name <> '' THEN ' ' || left(u.last_name, 1) || '.' ELSE '' END AS reviewer,
               count(*) OVER () AS total_count
          FROM reviews r JOIN users u ON u.id = r.user_id
         WHERE r.product_id = :pid AND (CAST(:rating AS int) IS NULL OR r.rating = :rating)
         ORDER BY r.created_at DESC, r.id
         LIMIT :limit OFFSET :offset
    """), {"pid": p["id"], "rating": rating, "limit": page_size,
           "offset": (page - 1) * page_size}).mappings().all()
    total = rows[0]["total_count"] if rows else 0
    return Page[ReviewOut](items=[ReviewOut(**{k: v for k, v in r.items() if k != "total_count"})
                                  for r in rows], total=total, page=page, page_size=page_size)


@router.get("/products/{slug}/related", response_model=list[ProductCard])
def related_products(slug: str, limit: int = Query(default=8, ge=1, le=24),
                     db: Session = Depends(get_db)):
    p = _get_product_or_404(db, slug)
    rows = db.execute(text(_LIST_SQL + """
        AND p.category_id = :cat AND p.id <> :pid
        ORDER BY p.review_count DESC, p.avg_rating DESC, p.id LIMIT :limit
    """), {"cat": p["category_id"], "pid": p["id"], "limit": limit}).mappings().all()
    return [_card(r) for r in rows]