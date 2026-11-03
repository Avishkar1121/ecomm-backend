from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class SellerRepository:
    @staticmethod
    def get_profile(db: Session, seller_id: UUID):
        return db.execute(text("""SELECT user_id, store_name, store_slug, description, logo_url, gstin,
                   verification_status, commission_rate, created_at, updated_at
              FROM seller_profiles WHERE user_id = :seller_id"""), {"seller_id": seller_id}).mappings().first()

    @staticmethod
    def create_profile(db: Session, seller_id: UUID, payload: dict):
        try:
            row = db.execute(text("""INSERT INTO seller_profiles
                (user_id, store_name, store_slug, description, logo_url, gstin)
                VALUES (:seller_id, :store_name, :store_slug, :description, :logo_url, :gstin)
                RETURNING user_id, store_name, store_slug, description, logo_url, gstin,
                          verification_status, commission_rate, created_at, updated_at"""),
                {"seller_id": seller_id, **payload}).mappings().one()
            role = db.execute(text("""INSERT INTO user_roles (user_id, role_id)
                SELECT :user_id, id FROM roles WHERE name = 'seller'
                ON CONFLICT DO NOTHING RETURNING user_id"""), {"user_id": seller_id}).first()
            if not role and not db.execute(text("SELECT 1 FROM roles WHERE name='seller'")).first():
                raise RuntimeError("The 'seller' role is missing from the roles table")
            db.commit()
            return row
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def update_profile(db: Session, seller_id: UUID, payload: dict):
        if not payload:
            return SellerRepository.get_profile(db, seller_id)
        allowed = {"store_name", "store_slug", "description", "logo_url", "gstin"}
        payload = {k: v for k, v in payload.items() if k in allowed}
        assignments = ", ".join(f"{key} = :{key}" for key in payload)
        try:
            row = db.execute(text(f"""UPDATE seller_profiles SET {assignments}, updated_at=now()
                WHERE user_id=:seller_id RETURNING user_id, store_name, store_slug, description,
                logo_url, gstin, verification_status, commission_rate, created_at, updated_at"""),
                {"seller_id": seller_id, **payload}).mappings().first()
            db.commit()
            return row
        except Exception:
            db.rollback(); raise

    @staticmethod
    def get_category(db: Session, category_id: UUID):
        return db.execute(text("SELECT id FROM categories WHERE id=:id AND is_active=TRUE"), {"id": category_id}).first()

    @staticmethod
    def brand_exists(db: Session, brand_id: UUID):
        return db.execute(text("SELECT id FROM brands WHERE id=:id"), {"id": brand_id}).first()

    @staticmethod
    def create_product(db: Session, seller_id: UUID, payload: dict):
        variants = payload.pop("variants", [])
        try:
            row = db.execute(text("""INSERT INTO products
                (seller_id, category_id, brand_id, title, slug, description, status)
                VALUES (:seller_id,:category_id,:brand_id,:title,:slug,:description,:status)
                RETURNING id"""), {"seller_id": seller_id, **payload}).mappings().one()
            for variant in variants:
                db.execute(text("""INSERT INTO product_variants
                    (product_id,sku,name,mrp,price,stock_qty,is_active)
                    VALUES (:product_id,:sku,:name,:mrp,:price,:stock_qty,:is_active)"""),
                    {"product_id": row["id"], **variant})
            product = SellerRepository.get_product(db, seller_id, row["id"])
            db.commit()
            return product
        except Exception:
            db.rollback(); raise

    @staticmethod
    def _product_select():
        return """SELECT p.id,p.seller_id,p.category_id,c.name AS category_name,p.brand_id,b.name AS brand_name,
            p.title,p.slug,p.description,p.status,p.avg_rating,p.review_count,p.created_at,p.updated_at,
            COALESCE(v.variants_count,0)::integer AS variants_count,
            COALESCE(v.total_stock,0)::integer AS total_stock,
            COALESCE(v.total_stock,0)>0 AS in_stock,img.url AS primary_image_url
            FROM products p JOIN categories c ON c.id=p.category_id LEFT JOIN brands b ON b.id=p.brand_id
            LEFT JOIN LATERAL (SELECT COUNT(*) AS variants_count,COALESCE(SUM(stock_qty),0) AS total_stock
                FROM product_variants pv WHERE pv.product_id=p.id AND pv.is_active=TRUE) v ON TRUE
            LEFT JOIN LATERAL (SELECT url FROM product_images pi WHERE pi.product_id=p.id
                AND pi.is_primary=TRUE ORDER BY pi.sort_order LIMIT 1) img ON TRUE"""

    @staticmethod
    def list_products(db: Session, seller_id: UUID, page: int, page_size: int):
        rows = db.execute(text(SellerRepository._product_select() + """ WHERE p.seller_id=:seller_id
            AND p.deleted_at IS NULL ORDER BY p.created_at DESC,p.id LIMIT :limit OFFSET :offset"""),
            {"seller_id": seller_id, "limit": page_size, "offset": (page-1)*page_size}).mappings().all()
        total = db.execute(text("SELECT COUNT(*) FROM products WHERE seller_id=:seller_id AND deleted_at IS NULL"),
            {"seller_id": seller_id}).scalar_one()
        return rows, total

    @staticmethod
    def get_product(db: Session, seller_id: UUID, product_id: UUID):
        return db.execute(text(SellerRepository._product_select() + " WHERE p.id=:product_id AND p.seller_id=:seller_id AND p.deleted_at IS NULL"),
            {"product_id": product_id, "seller_id": seller_id}).mappings().first()

    @staticmethod
    def update_product(db: Session, seller_id: UUID, product_id: UUID, payload: dict):
        if not payload: return SellerRepository.get_product(db, seller_id, product_id)
        allowed = {"category_id", "brand_id", "title", "slug", "description", "status"}
        payload = {k:v for k,v in payload.items() if k in allowed}
        assignments = ",".join(f"{k}=:{k}" for k in payload)
        try:
            row = db.execute(text(f"UPDATE products SET {assignments},updated_at=now() WHERE id=:product_id AND seller_id=:seller_id AND deleted_at IS NULL RETURNING id"),
                {"product_id":product_id,"seller_id":seller_id,**payload}).first()
            if not row: db.rollback(); return None
            db.commit(); return SellerRepository.get_product(db,seller_id,product_id)
        except Exception:
            db.rollback(); raise

    @staticmethod
    def archive_product(db: Session, seller_id: UUID, product_id: UUID):
        try:
            row=db.execute(text("UPDATE products SET deleted_at=now(),status='archived' WHERE id=:product_id AND seller_id=:seller_id AND deleted_at IS NULL RETURNING id"),
                {"product_id":product_id,"seller_id":seller_id}).first()
            db.commit(); return row is not None
        except Exception: db.rollback(); raise

    @staticmethod
    def list_variants(db: Session, seller_id: UUID, product_id: UUID):
        return db.execute(text("""SELECT pv.id,pv.product_id,pv.sku,pv.name,pv.mrp,pv.price,pv.stock_qty,pv.is_active,pv.created_at,pv.updated_at
            FROM product_variants pv JOIN products p ON p.id=pv.product_id
            WHERE p.id=:product_id AND p.seller_id=:seller_id AND p.deleted_at IS NULL ORDER BY pv.created_at,pv.id"""),
            {"product_id":product_id,"seller_id":seller_id}).mappings().all()

    @staticmethod
    def create_variant(db: Session, product_id: UUID, payload: dict):
        try:
            row=db.execute(text("""INSERT INTO product_variants(product_id,sku,name,mrp,price,stock_qty,is_active)
                VALUES(:product_id,:sku,:name,:mrp,:price,:stock_qty,:is_active)
                RETURNING id,product_id,sku,name,mrp,price,stock_qty,is_active,created_at,updated_at"""),
                {"product_id":product_id,**payload}).mappings().one(); db.commit(); return row
        except Exception: db.rollback(); raise

    @staticmethod
    def update_variant(db: Session, seller_id: UUID, product_id: UUID, variant_id: UUID, payload: dict):
        if not payload: return db.execute(text("SELECT pv.id,pv.product_id,pv.sku,pv.name,pv.mrp,pv.price,pv.stock_qty,pv.is_active,pv.created_at,pv.updated_at FROM product_variants pv JOIN products p ON p.id=pv.product_id WHERE pv.id=:vid AND p.id=:pid AND p.seller_id=:sid AND p.deleted_at IS NULL"), {"vid":variant_id,"pid":product_id,"sid":seller_id}).mappings().first()
        allowed={"sku","name","mrp","price","stock_qty","is_active"}; payload={k:v for k,v in payload.items() if k in allowed}
        assignments=",".join(f"{k}=:{k}" for k in payload)
        try:
            row=db.execute(text(f"""UPDATE product_variants pv SET {assignments},updated_at=now() FROM products p
                WHERE pv.product_id=p.id AND pv.id=:vid AND p.id=:pid AND p.seller_id=:sid AND p.deleted_at IS NULL
                RETURNING pv.id,pv.product_id,pv.sku,pv.name,pv.mrp,pv.price,pv.stock_qty,pv.is_active,pv.created_at,pv.updated_at"""),
                {"vid":variant_id,"pid":product_id,"sid":seller_id,**payload}).mappings().first()
            db.commit(); return row
        except Exception: db.rollback(); raise

    @staticmethod
    def delete_variant(db: Session, seller_id: UUID, product_id: UUID, variant_id: UUID):
        try:
            row=db.execute(text("""DELETE FROM product_variants pv USING products p WHERE pv.product_id=p.id
                AND pv.id=:vid AND p.id=:pid AND p.seller_id=:sid AND p.deleted_at IS NULL RETURNING pv.id"""),
                {"vid":variant_id,"pid":product_id,"sid":seller_id}).first(); db.commit(); return row is not None
        except Exception: db.rollback(); raise

    @staticmethod
    def list_images(db: Session, seller_id: UUID, product_id: UUID):
        return db.execute(text("""SELECT pi.id,pi.product_id,pi.url,pi.alt_text,pi.sort_order,pi.is_primary
            FROM product_images pi JOIN products p ON p.id=pi.product_id WHERE p.id=:pid AND p.seller_id=:sid
            AND p.deleted_at IS NULL ORDER BY pi.is_primary DESC,pi.sort_order,pi.id"""),
            {"pid":product_id,"sid":seller_id}).mappings().all()

    @staticmethod
    def create_image(db: Session, product_id: UUID, payload: dict):
        try:
            if payload.get("is_primary"):
                db.execute(text("UPDATE product_images SET is_primary=FALSE WHERE product_id=:pid"), {"pid":product_id})
            row=db.execute(text("""INSERT INTO product_images(product_id,url,alt_text,sort_order,is_primary)
                VALUES(:product_id,:url,:alt_text,:sort_order,:is_primary)
                RETURNING id,product_id,url,alt_text,sort_order,is_primary"""),
                {"product_id":product_id,**payload}).mappings().one(); db.commit(); return row
        except Exception: db.rollback(); raise

    @staticmethod
    def delete_image(db: Session, seller_id: UUID, product_id: UUID, image_id: UUID):
        try:
            row=db.execute(text("""DELETE FROM product_images pi USING products p WHERE pi.product_id=p.id
                AND pi.id=:iid AND p.id=:pid AND p.seller_id=:sid AND p.deleted_at IS NULL RETURNING pi.id"""),
                {"iid":image_id,"pid":product_id,"sid":seller_id}).first(); db.commit(); return row is not None
        except Exception: db.rollback(); raise

    @staticmethod
    def set_primary_image(db: Session, seller_id: UUID, product_id: UUID, image_id: UUID):
        try:
            exists=db.execute(text("""SELECT pi.id FROM product_images pi JOIN products p ON p.id=pi.product_id
                WHERE pi.id=:iid AND p.id=:pid AND p.seller_id=:sid AND p.deleted_at IS NULL"""),
                {"iid":image_id,"pid":product_id,"sid":seller_id}).first()
            if not exists: return None
            db.execute(text("UPDATE product_images SET is_primary=FALSE WHERE product_id=:pid"), {"pid":product_id})
            db.execute(text("UPDATE product_images SET is_primary=TRUE WHERE id=:iid"), {"iid":image_id})
            db.commit(); return True
        except Exception: db.rollback(); raise
