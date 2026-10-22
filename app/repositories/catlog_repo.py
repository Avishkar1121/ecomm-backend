from __future__ import annotations

from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session


_APPROVED_SELLER = "sp.verification_status = 'approved'"
_PRODUCT_EXTRA_COLUMNS: set[str] | None = None


def _product_extra_columns(db: Session) -> set[str]:
    """Which of products.features / products.specs actually exist (checked once per process)."""
    global _PRODUCT_EXTRA_COLUMNS
    if _PRODUCT_EXTRA_COLUMNS is None:
        rows = db.execute(text("""
            SELECT column_name FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'products'
               AND column_name IN ('features', 'specs')
        """)).scalars().all()
        _PRODUCT_EXTRA_COLUMNS = set(rows)
    return _PRODUCT_EXTRA_COLUMNS


class catlogRepository:
    @staticmethod
    def _execute(db: Session, sql: str, params: dict | None = None):
        """Execute text SQL, expanding the brand slug list when present."""
        params = params or {}
        statement = text(sql)
        if "brand_slugs" in params:
            statement = statement.bindparams(bindparam("brand_slugs", expanding=True))
        return db.execute(statement, params)

    @staticmethod
    def list_categories(db: Session):
        return db.execute(text("""
            SELECT id, parent_id, name, slug, sort_order, is_active
              FROM categories
             WHERE is_active = TRUE
             ORDER BY sort_order, name
        """)).mappings().all()

   
    @staticmethod
    def list_brands(db: Session, category: str | None = None):
        sql = """
            SELECT DISTINCT b.id, b.name, b.slug, b.logo_url
            FROM public.brands b
            JOIN public.products p ON p.brand_id = b.id
            JOIN public.categories c ON c.id = p.category_id
            JOIN public.seller_profiles sp ON sp.user_id = p.seller_id
            WHERE p.status = 'active'
              AND p.deleted_at IS NULL
              AND c.is_active = TRUE
              AND sp.verification_status = 'approved'
              AND EXISTS (
                  SELECT 1
                  FROM public.product_variants pv
                  WHERE pv.product_id = p.id
                    AND pv.is_active = TRUE
              )
        """
        params: dict = {}

        if category and category.strip():
            sql += """
                AND (
                    LOWER(c.name) = LOWER(:category)
                    OR LOWER(c.slug) = LOWER(:category)
                    OR c.parent_id IN (
                        SELECT id
                        FROM public.categories
                        WHERE LOWER(name) = LOWER(:category)
                           OR LOWER(slug) = LOWER(:category)
                    )
                )
            """
            params["category"] = category.strip()

        sql += " ORDER BY b.name"
        return db.execute(text(sql), params).mappings().all()

    @staticmethod
    def list_products(db: Session, sql: str, params: dict):
        return catlogRepository._execute(db, sql, params).mappings().all()

    @staticmethod
    def count_products(db: Session, sql: str, params: dict):
        # The service derives this COUNT query from the exact listing filters,
        # so pagination totals cannot disagree with the returned product list.
        return catlogRepository._execute(db, sql, params).scalar()

    @staticmethod
    def get_product_row(db: Session, slug: str):
        extra = _product_extra_columns(db)
        features = "p.features" if "features" in extra else "NULL::text[] AS features"
        specs = "p.specs" if "specs" in extra else "NULL::jsonb AS specs"
        return db.execute(text(f"""
            SELECT
                p.id,
                p.slug,
                p.title,
                p.description,
                {features},
                {specs},
                p.category_id,
                c.name AS category,
                c.slug AS category_slug,
                b.name AS brand,
                sp.store_name AS seller_store,
                sp.store_slug AS seller_slug
            FROM public.products p
            JOIN public.categories c
                ON c.id = p.category_id
            LEFT JOIN public.brands b
                ON b.id = p.brand_id
            JOIN public.seller_profiles sp
                ON sp.user_id = p.seller_id
            WHERE p.slug = :slug
            AND p.status = 'active'
            AND p.deleted_at IS NULL
            AND c.is_active = TRUE
            AND {_APPROVED_SELLER}
        """), {"slug": slug}).mappings().first()

    @staticmethod
    def get_product_images(db: Session, product_id: UUID):
        return db.execute(text("""
            SELECT url, alt_text, is_primary
              FROM product_images
             WHERE product_id = :product_id
             ORDER BY is_primary DESC, sort_order
        """), {"product_id": product_id}).mappings().all()

    @staticmethod
    def get_product_variants(db: Session, product_id: UUID):
        return db.execute(text("""
            SELECT id, sku, name, price, mrp, stock_qty, is_active
              FROM product_variants
             WHERE product_id = :product_id
               AND is_active = TRUE
             ORDER BY price, name, id
        """), {"product_id": product_id}).mappings().all()

    @staticmethod
    def get_product_reviews(db: Session, product_id: UUID, rating: int | None, page: int, page_size: int):
        sql = """
            SELECT r.id, r.rating, r.title, r.body, r.created_at,
                   u.first_name || CASE
                       WHEN u.last_name <> '' THEN ' ' || left(u.last_name, 1) || '.'
                       ELSE ''
                   END AS reviewer,
                   count(*) OVER () AS total_count
              FROM reviews r
              JOIN users u ON u.id = r.user_id
             WHERE r.product_id = :product_id
        """
        params: dict = {
            "product_id": product_id,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }

        if rating is not None:
            sql += " AND r.rating = :rating "
            params["rating"] = rating

        sql += " ORDER BY r.created_at DESC, r.id LIMIT :limit OFFSET :offset "
        return db.execute(text(sql), params).mappings().all()

    @staticmethod
    def count_reviews(db: Session, product_id: UUID, rating: int | None = None):
        sql = """
            SELECT COUNT(*)
              FROM reviews r
             WHERE r.product_id = :product_id
        """
        params = {"product_id": product_id}
        if rating is not None:
            sql += " AND r.rating = :rating "
            params["rating"] = rating
        return db.execute(text(sql), params).scalar()

    @staticmethod
    def get_rating_breakdown(db: Session, product_id: UUID):
        # Calculate directly from reviews: the base schema does not define a view.
        return db.execute(text("""
            SELECT COUNT(*)::integer AS total_reviews,
                   COALESCE(ROUND(AVG(rating)::numeric, 2), 0)::float AS avg_rating,
                   COUNT(*) FILTER (WHERE rating = 5)::integer AS five_star,
                   COUNT(*) FILTER (WHERE rating = 4)::integer AS four_star,
                   COUNT(*) FILTER (WHERE rating = 3)::integer AS three_star,
                   COUNT(*) FILTER (WHERE rating = 2)::integer AS two_star,
                   COUNT(*) FILTER (WHERE rating = 1)::integer AS one_star
              FROM reviews
             WHERE product_id = :product_id
        """), {"product_id": product_id}).mappings().first()

    @staticmethod
    def get_related_products(db: Session, category_id: UUID, product_id: UUID, limit: int):
        return db.execute(text("""
            SELECT p.id, p.slug, p.title,
                   c.name AS category, c.slug AS category_slug, b.name AS brand,
                   p.avg_rating, p.review_count,
                   v.price, v.mrp, v.in_stock,
                   img.url AS image_url
              FROM products p
              JOIN categories c ON c.id = p.category_id
              JOIN seller_profiles sp ON sp.user_id = p.seller_id
              LEFT JOIN brands b ON b.id = p.brand_id
              JOIN LATERAL (
                  SELECT min(pv.price) AS price,
                         (array_agg(pv.mrp ORDER BY pv.price, pv.id))[1] AS mrp,
                         bool_or(pv.stock_qty > 0) AS in_stock
                    FROM product_variants pv
                   WHERE pv.product_id = p.id
                     AND pv.is_active
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
               AND sp.verification_status = 'approved'
               AND p.category_id = :category_id
               AND p.id <> :product_id
             ORDER BY p.review_count DESC, p.avg_rating DESC, p.id
             LIMIT :limit
        """), {"category_id": category_id, "product_id": product_id, "limit": limit}).mappings().all()

    @staticmethod
    def create_category(db: Session, payload: dict):
        row = db.execute(text("""
            INSERT INTO categories (parent_id, name, slug, sort_order, is_active)
            VALUES (:parent_id, :name, :slug, :sort_order, :is_active)
            RETURNING id, parent_id, name, slug, sort_order, is_active
        """), payload).mappings().first()
        db.commit()
        return row

    @staticmethod
    def update_category(db: Session, category_id: UUID, payload: dict):
        allowed = {"parent_id", "name", "slug", "sort_order", "is_active"}
        payload = {key: value for key, value in payload.items() if key in allowed}
        sets = [f"{key} = :{key}" for key in payload]
        if not sets:
            return None
        params = {**payload, "id": category_id}
        sql = (
            f"UPDATE categories SET {', '.join(sets)} "
            "WHERE id = :id "
            "RETURNING id, parent_id, name, slug, sort_order, is_active"
        )
        row = db.execute(text(sql), params).mappings().first()
        db.commit()
        return row

    @staticmethod
    def delete_category(db: Session, category_id: UUID):
        row = db.execute(text("""
            UPDATE categories SET is_active = FALSE
             WHERE id = :id AND is_active = TRUE
             RETURNING id
        """), {"id": category_id}).mappings().first()
        db.commit()
        return row is not None

    @staticmethod
    def create_brand(db: Session, payload: dict):
        row = db.execute(text("""
            INSERT INTO brands (name, slug, logo_url)
            VALUES (:name, :slug, :logo_url)
            RETURNING id, name, slug, logo_url
        """), payload).mappings().first()
        db.commit()
        return row

    @staticmethod
    def update_brand(db: Session, brand_id: UUID, payload: dict):
        allowed = {"name", "slug", "logo_url"}
        payload = {key: value for key, value in payload.items() if key in allowed}
        sets = [f"{key} = :{key}" for key in payload]
        if not sets:
            return None
        params = {**payload, "id": brand_id}
        sql = (
            f"UPDATE brands SET {', '.join(sets)} "
            "WHERE id = :id "
            "RETURNING id, name, slug, logo_url"
        )
        row = db.execute(text(sql), params).mappings().first()
        db.commit()
        return row

    @staticmethod
    def delete_brand(db: Session, brand_id: UUID):
        row = db.execute(
            text("DELETE FROM brands WHERE id = :id RETURNING id"),
            {"id": brand_id},
        ).mappings().first()
        db.commit()
        return row is not None

    @staticmethod
    def create_product(db: Session, payload: dict):
        row = db.execute(text("""
            INSERT INTO products (
                seller_id, category_id, brand_id, title, slug, description, status
            )
            VALUES (
                :seller_id, :category_id, :brand_id, :title, :slug, :description, :status
            )
            RETURNING id, seller_id, category_id, brand_id, title, slug, description,
                      status, avg_rating, review_count, created_at, updated_at, deleted_at
        """), payload).mappings().first()
        db.commit()
        return row

    @staticmethod
    def update_product(db: Session, product_id: UUID, payload: dict):
        allowed = {"seller_id", "category_id", "brand_id", "title", "slug", "description", "status"}
        payload = {key: value for key, value in payload.items() if key in allowed}
        # Null is valid only for nullable product columns; ignore invalid nulls
        # for required columns instead of issuing a broken UPDATE.
        for required in ("seller_id", "category_id", "title", "slug", "status"):
            if required in payload and payload[required] is None:
                raise ValueError(f"{required} cannot be null")
        sets = [f"{key} = :{key}" for key in payload]
        if not sets:
            return None
        params = {**payload, "id": product_id}
        sql = (
            f"UPDATE products SET {', '.join(sets)} "
            "WHERE id = :id "
            "RETURNING id, seller_id, category_id, brand_id, title, slug, description, "
            "status, avg_rating, review_count, created_at, updated_at, deleted_at"
        )
        row = db.execute(text(sql), params).mappings().first()
        db.commit()
        return row
    
    @staticmethod
    def delete_product(db: Session, product_id: UUID):
        row = db.execute(text("""
            UPDATE products
               SET deleted_at = now(),
                   status = 'archived'
             WHERE id = :id
             RETURNING id
        """), {"id": product_id}).mappings().first()
        db.commit()
        return row is not None