from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repositories.admin_repo import AdminRepository


class AdminService:
    @staticmethod
    def list_sellers(db: Session, status_filter: str, page: int, page_size: int):
        rows, total = AdminRepository.list_sellers(
            db, status_filter, page_size, (page - 1) * page_size
        )
        return {"items": rows, "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def get_seller(db: Session, seller_id: UUID):
        row = AdminRepository.get_seller(db, seller_id)
        if not row:
            raise HTTPException(404, "Seller application not found")
        return row

    @staticmethod
    def _review(db: Session, seller_id: UUID, admin_id: UUID, new_status: str,
                reason: str | None = None):
        try:
            current = AdminRepository.lock_seller(db, seller_id)
            if not current:
                raise HTTPException(404, "Seller application not found")
            if current["verification_status"] != "pending":
                raise HTTPException(
                    409,
                    f"Only pending applications can be reviewed; current status is '{current['verification_status']}'"
                )
            if new_status == "approved":
                AdminRepository.assign_seller_role(db, seller_id)
            else:
                AdminRepository.remove_seller_role(db, seller_id)
            result = AdminRepository.set_status(db, seller_id, admin_id, new_status, reason)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def approve_seller(db: Session, seller_id: UUID, admin_id: UUID):
        return AdminService._review(db, seller_id, admin_id, "approved")

    @staticmethod
    def reject_seller(db: Session, seller_id: UUID, admin_id: UUID, reason: str):
        return AdminService._review(db, seller_id, admin_id, "rejected", reason)

    @staticmethod
    def suspend_seller(db: Session, seller_id: UUID, admin_id: UUID):
        try:
            current = AdminRepository.lock_seller(db, seller_id)
            if not current:
                raise HTTPException(404, "Seller application not found")
            if current["verification_status"] != "approved":
                raise HTTPException(409, "Only approved sellers can be suspended")
            AdminRepository.remove_seller_role(db, seller_id)
            result = AdminRepository.set_status(db, seller_id, admin_id, "suspended")
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
