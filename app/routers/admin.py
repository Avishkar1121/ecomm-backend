from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.deps import require_role
from app.schemas.admin import (
    AdminActionOut, SellerApplicationOut, SellerApplicationPage, SellerRejectIn,
)
from app.services.admin_service import AdminService

router = APIRouter(prefix="/admin", tags=["admin"])
admin_only = require_role("admin")


@router.get("/sellers", response_model=SellerApplicationPage)
def list_seller_applications(
    status_filter: str = Query(default="pending", alias="status", pattern="^(pending|approved|rejected|suspended|all)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: dict = Depends(admin_only),
):
    return AdminService.list_sellers(db, status_filter, page, page_size)


@router.get("/sellers/{seller_id}", response_model=SellerApplicationOut)
def get_seller_application(
    seller_id: UUID,
    db: Session = Depends(get_db),
    admin: dict = Depends(admin_only),
):
    return AdminService.get_seller(db, seller_id)


@router.patch("/sellers/{seller_id}/approve", response_model=AdminActionOut)
def approve_seller(
    seller_id: UUID,
    db: Session = Depends(get_db),
    admin: dict = Depends(admin_only),
):
    return AdminService.approve_seller(db, seller_id, admin["id"])


@router.patch("/sellers/{seller_id}/reject", response_model=AdminActionOut)
def reject_seller(
    seller_id: UUID,
    payload: SellerRejectIn,
    db: Session = Depends(get_db),
    admin: dict = Depends(admin_only),
):
    return AdminService.reject_seller(db, seller_id, admin["id"], payload.reason)


@router.patch("/sellers/{seller_id}/suspend", response_model=AdminActionOut)
def suspend_seller(
    seller_id: UUID,
    db: Session = Depends(get_db),
    admin: dict = Depends(admin_only),
):
    return AdminService.suspend_seller(db, seller_id, admin["id"])
