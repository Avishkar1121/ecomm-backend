from uuid import UUID
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.deps import require_role, get_current_user
from app.schemas.seller import (SellerProfileCreate,SellerProfileOut,SellerProfileUpdate,
    SellerProductCreate,SellerProductOut,SellerProductPage,SellerProductUpdate,
    SellerVariantCreate,SellerVariantUpdate,SellerVariantOut,SellerImageCreate,SellerImageOut)
from app.services.seller_service import SellerService

router=APIRouter(prefix="/seller",tags=["seller"])
seller_only=require_role("seller")

@router.get("/profile",response_model=SellerProfileOut)
def get_seller_profile(db:Session=Depends(get_db),user:dict=Depends(get_current_user)):
    return SellerService.get_profile(db,user["id"])

@router.post("/profile",response_model=SellerProfileOut,status_code=status.HTTP_201_CREATED)
def create_seller_profile(payload:SellerProfileCreate,db:Session=Depends(get_db),user:dict=Depends(get_current_user)):
    # Profile creation also assigns the seller role in the same database transaction.
    return SellerService.create_profile(db,user["id"],payload.model_dump())

@router.put("/profile",response_model=SellerProfileOut)
def update_seller_profile(payload:SellerProfileUpdate,db:Session=Depends(get_db),user:dict=Depends(seller_only)):
    return SellerService.update_profile(db,user["id"],payload.model_dump(exclude_unset=True))

@router.get("/products",response_model=SellerProductPage)
def list_seller_products(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),db:Session=Depends(get_db),user:dict=Depends(seller_only)):
    return SellerService.list_products(db,user["id"],page,page_size)

@router.post("/products",response_model=SellerProductOut,status_code=201)
def create_seller_product(payload:SellerProductCreate,db:Session=Depends(get_db),user:dict=Depends(seller_only)):
    return SellerService.create_product(db,user["id"],payload.model_dump())

@router.get("/products/{product_id}",response_model=SellerProductOut)
def get_seller_product(product_id:UUID,db:Session=Depends(get_db),user:dict=Depends(seller_only)):
    return SellerService.get_product(db,user["id"],product_id)

@router.put("/products/{product_id}",response_model=SellerProductOut)
def update_seller_product(product_id:UUID,payload:SellerProductUpdate,db:Session=Depends(get_db),user:dict=Depends(seller_only)):
    return SellerService.update_product(db,user["id"],product_id,payload.model_dump(exclude_unset=True))

@router.delete("/products/{product_id}",status_code=204)
def archive_seller_product(product_id:UUID,db:Session=Depends(get_db),user:dict=Depends(seller_only)):
    SellerService.archive_product(db,user["id"],product_id); return Response(status_code=204)

@router.get("/products/{product_id}/variants",response_model=list[SellerVariantOut])
def list_variants(product_id:UUID,db:Session=Depends(get_db),user:dict=Depends(seller_only)):
    return SellerService.list_variants(db,user["id"],product_id)

@router.post("/products/{product_id}/variants",response_model=SellerVariantOut,status_code=201)
def create_variant(product_id:UUID,payload:SellerVariantCreate,db:Session=Depends(get_db),user:dict=Depends(seller_only)):
    return SellerService.create_variant(db,user["id"],product_id,payload.model_dump())

@router.patch("/products/{product_id}/variants/{variant_id}",response_model=SellerVariantOut)
def update_variant(product_id:UUID,variant_id:UUID,payload:SellerVariantUpdate,db:Session=Depends(get_db),user:dict=Depends(seller_only)):
    return SellerService.update_variant(db,user["id"],product_id,variant_id,payload.model_dump(exclude_unset=True))

@router.delete("/products/{product_id}/variants/{variant_id}",status_code=204)
def delete_variant(product_id:UUID,variant_id:UUID,db:Session=Depends(get_db),user:dict=Depends(seller_only)):
    SellerService.delete_variant(db,user["id"],product_id,variant_id); return Response(status_code=204)

@router.get("/products/{product_id}/images",response_model=list[SellerImageOut])
def list_images(product_id:UUID,db:Session=Depends(get_db),user:dict=Depends(seller_only)):
    return SellerService.list_images(db,user["id"],product_id)

@router.post("/products/{product_id}/images",response_model=SellerImageOut,status_code=201)
def create_image(product_id:UUID,payload:SellerImageCreate,db:Session=Depends(get_db),user:dict=Depends(seller_only)):
    return SellerService.create_image(db,user["id"],product_id,payload.model_dump())

@router.put("/products/{product_id}/images/{image_id}/primary",response_model=dict)
def set_primary_image(product_id:UUID,image_id:UUID,db:Session=Depends(get_db),user:dict=Depends(seller_only)):
    SellerService.set_primary_image(db,user["id"],product_id,image_id); return {"success":True,"message":"Primary image updated"}

@router.delete("/products/{product_id}/images/{image_id}",status_code=204)
def delete_image(product_id:UUID,image_id:UUID,db:Session=Depends(get_db),user:dict=Depends(seller_only)):
    SellerService.delete_image(db,user["id"],product_id,image_id); return Response(status_code=204)
