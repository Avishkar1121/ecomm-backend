from uuid import UUID
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.repositories.seller_repo import SellerRepository as Repo


def _db_conflict(exc: IntegrityError):
    state = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    if state == "23505":
        msg = "Store slug, product slug, or SKU already exists."
    elif state == "23503": msg = "A referenced record does not exist or is still in use."
    else: msg = "The submitted data conflicts with a database constraint."
    raise HTTPException(status_code=409 if state == "23505" else 400, detail=msg) from exc

class SellerService:
    @staticmethod
    def get_profile(db, seller_id):
        row=Repo.get_profile(db,seller_id)
        if not row: raise HTTPException(404,"Seller profile not found")
        return row

    @staticmethod
    def create_profile(db, seller_id, payload):
        if Repo.get_profile(db,seller_id): raise HTTPException(409,"Seller profile already exists")
        try: return Repo.create_profile(db,seller_id,payload)
        except IntegrityError as exc: _db_conflict(exc)

    @staticmethod
    def update_profile(db,seller_id,payload):
        try: row=Repo.update_profile(db,seller_id,payload)
        except IntegrityError as exc: _db_conflict(exc)
        if not row: raise HTTPException(404,"Seller profile not found")
        return row

    @staticmethod
    def _ensure_profile(db,seller_id):
        profile=Repo.get_profile(db,seller_id)
        if not profile: raise HTTPException(409,"Create your seller profile before managing products")
        return profile

    @staticmethod
    def _ensure_product(db,seller_id,product_id):
        SellerService._ensure_profile(db,seller_id)
        product=Repo.get_product(db,seller_id,product_id)
        if not product: raise HTTPException(404,"Product not found")
        return product

    @staticmethod
    def _validate_refs(db,payload):
        if payload.get("category_id") is not None and not Repo.get_category(db,payload["category_id"]): raise HTTPException(400,"Category does not exist or is inactive")
        if payload.get("brand_id") is not None and not Repo.brand_exists(db,payload["brand_id"]): raise HTTPException(400,"Brand does not exist")

    @staticmethod
    def _validate_status(profile,status):
        if status=="active" and profile["verification_status"]!="approved": raise HTTPException(403,"Only approved sellers can publish active products")

    @staticmethod
    def create_product(db,seller_id,payload):
        profile=SellerService._ensure_profile(db,seller_id); SellerService._validate_refs(db,payload); SellerService._validate_status(profile,payload.get("status"))
        if payload.get("status")=="active" and not payload.get("variants"): raise HTTPException(400,"At least one variant is required to publish a product")
        try: return Repo.create_product(db,seller_id,dict(payload))
        except IntegrityError as exc: _db_conflict(exc)

    @staticmethod
    def list_products(db,seller_id,page,page_size):
        SellerService._ensure_profile(db,seller_id); rows,total=Repo.list_products(db,seller_id,page,page_size)
        return {"items":rows,"total":total,"page":page,"page_size":page_size}

    @staticmethod
    def get_product(db,seller_id,product_id): return SellerService._ensure_product(db,seller_id,product_id)

    @staticmethod
    def update_product(db,seller_id,product_id,payload):
        profile=SellerService._ensure_profile(db,seller_id); SellerService._validate_refs(db,payload); SellerService._validate_status(profile,payload.get("status"))
        try: row=Repo.update_product(db,seller_id,product_id,payload)
        except IntegrityError as exc: _db_conflict(exc)
        if not row: raise HTTPException(404,"Product not found")
        return row

    @staticmethod
    def archive_product(db,seller_id,product_id):
        SellerService._ensure_profile(db,seller_id)
        if not Repo.archive_product(db,seller_id,product_id): raise HTTPException(404,"Product not found")

    @staticmethod
    def list_variants(db,seller_id,product_id):
        SellerService._ensure_product(db,seller_id,product_id); return Repo.list_variants(db,seller_id,product_id)

    @staticmethod
    def create_variant(db,seller_id,product_id,payload):
        product=SellerService._ensure_product(db,seller_id,product_id)
        try: return Repo.create_variant(db,product_id,payload)
        except IntegrityError as exc: _db_conflict(exc)

    @staticmethod
    def update_variant(db,seller_id,product_id,variant_id,payload):
        SellerService._ensure_product(db,seller_id,product_id)
        # Validate merged price/MRP so partial updates cannot violate the invariant.
        existing=next((v for v in Repo.list_variants(db,seller_id,product_id) if v["id"]==variant_id),None)
        if not existing: raise HTTPException(404,"Variant not found")
        mrp=payload.get("mrp",existing["mrp"]); price=payload.get("price",existing["price"])
        if price>mrp: raise HTTPException(400,"Variant selling price cannot exceed MRP")
        try: row=Repo.update_variant(db,seller_id,product_id,variant_id,payload)
        except IntegrityError as exc: _db_conflict(exc)
        if not row: raise HTTPException(404,"Variant not found")
        return row

    @staticmethod
    def delete_variant(db,seller_id,product_id,variant_id):
        SellerService._ensure_product(db,seller_id,product_id)
        if not Repo.delete_variant(db,seller_id,product_id,variant_id): raise HTTPException(404,"Variant not found")

    @staticmethod
    def list_images(db,seller_id,product_id):
        SellerService._ensure_product(db,seller_id,product_id); return Repo.list_images(db,seller_id,product_id)

    @staticmethod
    def create_image(db,seller_id,product_id,payload):
        SellerService._ensure_product(db,seller_id,product_id)
        try: return Repo.create_image(db,product_id,payload)
        except IntegrityError as exc: _db_conflict(exc)

    @staticmethod
    def delete_image(db,seller_id,product_id,image_id):
        SellerService._ensure_product(db,seller_id,product_id)
        if not Repo.delete_image(db,seller_id,product_id,image_id): raise HTTPException(404,"Product image not found")

    @staticmethod
    def set_primary_image(db,seller_id,product_id,image_id):
        SellerService._ensure_product(db,seller_id,product_id)
        if not Repo.set_primary_image(db,seller_id,product_id,image_id): raise HTTPException(404,"Product image not found")
