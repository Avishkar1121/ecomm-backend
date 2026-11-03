from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class AdminRepository:
    @staticmethod
    def list_sellers(db: Session, status_filter: str, limit: int, offset: int):
        params = {"status": status_filter, "limit": limit, "offset": offset}
        where = "(:status = 'all' OR sp.verification_status = :status)"
        items = db.execute(text(f"""
            SELECT sp.user_id, u.first_name, u.last_name, u.email, u.phone,
                   sp.store_name, sp.store_slug, sp.description, sp.logo_url, sp.gstin,
                   sp.verification_status, sp.commission_rate, sp.created_at, sp.updated_at,
                   sp.reviewed_by, sp.reviewed_at, sp.rejection_reason
              FROM seller_profiles sp
              JOIN users u ON u.id = sp.user_id
             WHERE {where} AND u.deleted_at IS NULL
             ORDER BY CASE WHEN sp.verification_status = 'pending' THEN 0 ELSE 1 END,
                      sp.created_at ASC, sp.user_id
             LIMIT :limit OFFSET :offset
        """), params).mappings().all()
        total = db.execute(text(f"""
            SELECT COUNT(*) FROM seller_profiles sp
              JOIN users u ON u.id = sp.user_id
             WHERE {where} AND u.deleted_at IS NULL
        """), {"status": status_filter}).scalar_one()
        return items, total

    @staticmethod
    def get_seller(db: Session, seller_id: UUID):
        return db.execute(text("""
            SELECT sp.user_id, u.first_name, u.last_name, u.email, u.phone,
                   sp.store_name, sp.store_slug, sp.description, sp.logo_url, sp.gstin,
                   sp.verification_status, sp.commission_rate, sp.created_at, sp.updated_at,
                   sp.reviewed_by, sp.reviewed_at, sp.rejection_reason
              FROM seller_profiles sp JOIN users u ON u.id = sp.user_id
             WHERE sp.user_id = :id AND u.deleted_at IS NULL
        """), {"id": seller_id}).mappings().first()

    @staticmethod
    def lock_seller(db: Session, seller_id: UUID):
        return db.execute(text("""
            SELECT user_id, verification_status FROM seller_profiles
             WHERE user_id = :id FOR UPDATE
        """), {"id": seller_id}).mappings().first()

    @staticmethod
    def set_status(db: Session, seller_id: UUID, admin_id: UUID, new_status: str,
                   rejection_reason: str | None = None):
        return db.execute(text("""
            UPDATE seller_profiles
               SET verification_status = :new_status,
                   reviewed_by = :admin_id,
                   reviewed_at = now(),
                   rejection_reason = :reason,
                   updated_at = now()
             WHERE user_id = :seller_id
         RETURNING user_id, verification_status, reviewed_by, reviewed_at, rejection_reason
        """), {"seller_id": seller_id, "admin_id": admin_id,
               "new_status": new_status, "reason": rejection_reason}).mappings().first()

    @staticmethod
    def assign_seller_role(db: Session, seller_id: UUID):
        result = db.execute(text("""
            INSERT INTO user_roles (user_id, role_id)
            SELECT :user_id, r.id FROM roles r WHERE r.name = 'seller'
            ON CONFLICT DO NOTHING
        """), {"user_id": seller_id})
        role_exists = db.execute(text("SELECT 1 FROM roles WHERE name = 'seller'")).first()
        if not role_exists:
            raise RuntimeError("The 'seller' role is missing from the roles table")

    @staticmethod
    def remove_seller_role(db: Session, seller_id: UUID):
        db.execute(text("""
            DELETE FROM user_roles ur USING roles r
             WHERE ur.role_id = r.id AND r.name = 'seller' AND ur.user_id = :user_id
        """), {"user_id": seller_id})
