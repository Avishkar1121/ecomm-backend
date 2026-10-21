from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repositories.catlog_repo import catlogRepository


SortKey = Literal["newest", "price_asc", "price_desc", "rating", "trending"]


class catlogService:
    _SORTS = {
        "newest": "p.created_at DESC, p.id",
        "price_asc": "v.price ASC, p.id",
        "price_desc": "v.price DESC, p.id",
        "rating": "p.avg_rating DESC, p.review_count DESC, p.id",
        "trending": "p.review_count DESC, p.avg_rating DESC, p.id",
    }

    @staticmethod
    def _discount(price: float, mrp: float) -> int:
        if not mrp or mrp <= price:
            return 0
        return round((mrp - price) / mrp * 100)

    @staticmethod
    def _like(term: str) -> str:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    @staticmethod
    def list_categories(db: Session):
        rows = catlogRepository.list_categories(db)
        return [dict(r) for r in rows]

    @staticmethod
    def list_brands(db: Session, category: str | None = None):
        rows = catlogRepository.list_brands(db, category=category)
        return [dict(r) for r in rows]

    @staticmethod
    def build_listing_query(
        q: str | None,
        category: str | None,
        brand: list[str],
        min_price: float | None,
        max_price: float | None,
        min_rating: float | None,
        in_stock: bool,
        sort: SortKey,
        page: int,
        page_size: int,
    ) -> tuple[str, dict]:
        sql = """
            SELECT p.id, p.slug, p.title,
                   c.name AS category, c.slug AS category_slug, b.name AS brand,
                   p.avg_rating, p.review_count,
                   v.price, v.mrp, v.in_stock,
                   img.url AS image_url
              FROM products p
              JOIN categories c ON c.id = p.category_id
              LEFT JOIN brands b ON b.id = p.brand_id
              JOIN LATERAL (
                    SELECT min(pv.price) AS price,
                           (array_agg(pv.mrp ORDER BY pv.price, pv.id))[1] AS mrp,
                           bool_or(pv.stock_qty > 0) AS in_stock
                      FROM product_variants pv
                     WHERE pv.product_id = p.id
                       AND pv.is_active = TRUE
              ) v ON v.price IS NOT NULL
              LEFT JOIN LATERAL (
                    SELECT pi.url
                      FROM product_images pi
                     WHERE pi.product_id = p.id
                       AND pi.is_primary = TRUE
                     ORDER BY pi.sort_order
                     LIMIT 1
              ) img ON true
             WHERE p.status = 'active'
               AND p.deleted_at IS NULL
               AND c.is_active = TRUE
        """
        params: dict = {}

        if q and q.strip():
            sql += " AND (p.title ILIKE :q OR b.name ILIKE :q)"
            params["q"] = catlogService._like(q.strip())

        if category:
            sql += """
                AND (
                    c.slug = :category
                    OR c.parent_id = (
                        SELECT id FROM categories WHERE slug = :category
                    )
                )
            """
            params["category"] = category

        if brand:
            sql += " AND b.slug IN :brand_slugs"
            params["brand_slugs"] = brand

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
            sql += " AND v.in_stock = TRUE"

        sql += f" ORDER BY {catlogService._SORTS[sort]}, p.id LIMIT :limit OFFSET :offset"
        params.update({"limit": page_size, "offset": (page - 1) * page_size})

        return sql, params

    @staticmethod
    def list_products(
        db: Session,
        q: str | None,
        category: str | None,
        brand: list[str],
        min_price: float | None,
        max_price: float | None,
        min_rating: float | None,
        in_stock: bool,
        sort: SortKey,
        page: int,
        page_size: int,
    ):
        sql, params = catlogService.build_listing_query(
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

        # Reuse the exact FROM/JOIN/WHERE section for count to keep all filters,
        # including the minimum active-variant price, consistent.
        from_index = sql.index(" FROM products p")
        order_index = sql.rfind(" ORDER BY ")
        count_sql = "SELECT COUNT(*)" + sql[from_index:order_index]
        total_params = {k: v for k, v in params.items() if k not in {"limit", "offset"}}

        rows = catlogRepository.list_products(db, sql, params)
        total = catlogRepository.count_products(db, count_sql, total_params) or 0

        items = []
        for row in rows:
            price = float(row["price"])
            mrp = float(row["mrp"])
            items.append({
                "id": row["id"],
                "slug": row["slug"],
                "title": row["title"],
                "category": row["category"],
                "category_slug": row["category_slug"],
                "brand": row["brand"],
                "image_url": row["image_url"],
                "price": price,
                "mrp": mrp,
                "discount_percent": catlogService._discount(price, mrp),
                "avg_rating": float(row["avg_rating"]),
                "review_count": row["review_count"],
                "in_stock": bool(row["in_stock"]),
            })

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    @staticmethod
    def get_product_detail(db: Session, slug: str):
        row = catlogRepository.get_product_row(db, slug)
        if not row:
            raise HTTPException(status_code=404, detail="Product not found")

        images = catlogRepository.get_product_images(db, row["id"])
        variants = catlogRepository.get_product_variants(db, row["id"])

        if not variants:
            raise HTTPException(status_code=404, detail="Product not found")

        variant_out = []
        for v in variants:
            price = float(v["price"])
            mrp = float(v["mrp"])
            if mrp < price:
                raise HTTPException(status_code=500, detail=f"Invalid variant pricing for product {row['id']}")
            variant_out.append({
                "id": v["id"],
                "sku": v["sku"],
                "name": v["name"],
                "price": price,
                "mrp": mrp,
                "stock_qty": v["stock_qty"],
                "in_stock": bool(v["stock_qty"] > 0),
            })

        cheapest = variant_out[0]
        rating = catlogRepository.get_rating_breakdown(db, row["id"])
        if not rating:
            rating = {
                "total_reviews": 0,
                "avg_rating": 0.0,
                "five_star": 0,
                "four_star": 0,
                "three_star": 0,
                "two_star": 0,
                "one_star": 0,
            }

        return {
            "id": row["id"],
            "slug": row["slug"],
            "title": row["title"],
            "description": row["description"],
            "category": row["category"],
            "category_slug": row["category_slug"],
            "brand": row["brand"],
            "seller_store": row["seller_store"],
            "seller_slug": row["seller_slug"],
            # The supplied database schema has no products.features/specs columns.
            "features": list(row["features"] or []),
            "specs": dict(row["specs"] or {}),
            "images": [
                {"url": i["url"], "alt_text": i["alt_text"], "is_primary": bool(i["is_primary"])}
                for i in images
            ],
            "variants": variant_out,
            "price": float(cheapest["price"]),
            "mrp": float(cheapest["mrp"]),
            "discount_percent": catlogService._discount(float(cheapest["price"]), float(cheapest["mrp"])),
            "in_stock": any(v["in_stock"] for v in variant_out),
            "rating": {
                "total_reviews": int(rating.get("total_reviews", 0)),
                "avg_rating": float(rating.get("avg_rating", 0) or 0),
                "five_star": int(rating.get("five_star", 0)),
                "four_star": int(rating.get("four_star", 0)),
                "three_star": int(rating.get("three_star", 0)),
                "two_star": int(rating.get("two_star", 0)),
                "one_star": int(rating.get("one_star", 0)),
            },
        }

    @staticmethod
    def get_rating_summary(db: Session, slug: str):
        row = catlogRepository.get_product_row(db, slug)
        if not row:
            raise HTTPException(status_code=404, detail="Product not found")

        summary = catlogRepository.get_rating_breakdown(db, row["id"])
        if not summary:
            return {
                "total_reviews": 0,
                "avg_rating": 0.0,
                "five_star": 0,
                "four_star": 0,
                "three_star": 0,
                "two_star": 0,
                "one_star": 0,
            }

        return {
            "total_reviews": int(summary.get("total_reviews", 0)),
            "avg_rating": float(summary.get("avg_rating", 0) or 0),
            "five_star": int(summary.get("five_star", 0)),
            "four_star": int(summary.get("four_star", 0)),
            "three_star": int(summary.get("three_star", 0)),
            "two_star": int(summary.get("two_star", 0)),
            "one_star": int(summary.get("one_star", 0)),
        }

    @staticmethod
    def get_product_reviews(db: Session, slug: str, rating: int | None, page: int, page_size: int):
        row = catlogRepository.get_product_row(db, slug)
        if not row:
            raise HTTPException(status_code=404, detail="Product not found")

        rows = catlogRepository.get_product_reviews(db, row["id"], rating, page, page_size)
        items = []
        for r in rows:
            items.append({
                "id": r["id"],
                "rating": r["rating"],
                "title": r["title"],
                "body": r["body"],
                "reviewer": r["reviewer"],
                "created_at": r["created_at"],
            })

        total = catlogRepository.count_reviews(db, row["id"], rating) or 0
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    @staticmethod
    def get_related_products(db: Session, slug: str, limit: int):
        row = catlogRepository.get_product_row(db, slug)
        if not row:
            raise HTTPException(status_code=404, detail="Product not found")

        rows = catlogRepository.get_related_products(db, row["category_id"], row["id"], limit)
        result = []
        for r in rows:
            price = float(r["price"])
            mrp = float(r["mrp"])
            result.append({
                "id": r["id"],
                "slug": r["slug"],
                "title": r["title"],
                "category": r["category"],
                "category_slug": r["category_slug"],
                "brand": r["brand"],
                "image_url": r["image_url"],
                "price": price,
                "mrp": mrp,
                "discount_percent": catlogService._discount(price, mrp),
                "avg_rating": float(r["avg_rating"]),
                "review_count": r["review_count"],
                "in_stock": bool(r["in_stock"]),
            })

        return result

    @staticmethod
    def create_category(db: Session, payload: dict):
        return catlogRepository.create_category(db, payload)

    @staticmethod
    def update_category(db: Session, category_id: str, payload: dict):
        return catlogRepository.update_category(db, category_id, payload)

    @staticmethod
    def delete_category(db: Session, category_id: str):
        return catlogRepository.delete_category(db, category_id)

    @staticmethod
    def create_brand(db: Session, payload: dict):
        return catlogRepository.create_brand(db, payload)

    @staticmethod
    def update_brand(db: Session, brand_id: str, payload: dict):
        return catlogRepository.update_brand(db, brand_id, payload)

    @staticmethod
    def delete_brand(db: Session, brand_id: str):
        return catlogRepository.delete_brand(db, brand_id)

    @staticmethod
    def create_product(db: Session, payload: dict):
        return catlogRepository.create_product(db, payload)

    @staticmethod
    def update_product(db: Session, product_id: str, payload: dict):
        return catlogRepository.update_product(db, product_id, payload)

    @staticmethod
    def delete_product(db: Session, product_id: str):
        return catlogRepository.delete_product(db, product_id)