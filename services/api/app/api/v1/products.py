"""Minimal product API needed to build a cart (Phase 5 API layer, the
part flagged missing in the phase-status report). Full CRUD/import is
still a follow-up — this is search + barcode lookup + a basic create,
enough for checkout (Phase 6/7) to have something real to sell."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import require_permission
from app.core.security import Principal
from app.db.session import get_db
from app.domain.catalog import Barcode, Product, Tax

router = APIRouter(prefix="/products", tags=["products"])


class ProductOut(BaseModel):
    id: int
    name: str
    sku: str | None
    price_minor: int
    currency: str
    unit: str
    is_weighted: bool
    tax_id: int | None
    # Added for the Phase 8 offline catalog rebuild: the Electron app
    # snapshots this into its local SQLite catalog so a sale can be priced
    # AND taxed correctly with zero network calls, instead of only caching
    # the price and having no way to compute tax offline.
    tax_rate_basis_points: int | None = None
    barcodes: list[str] = []

    class Config:
        from_attributes = True

    @classmethod
    def from_product(cls, product: Product, tax_by_id: dict[int, Tax]) -> "ProductOut":
        tax = tax_by_id.get(product.tax_id) if product.tax_id else None
        return cls(
            id=product.id,
            name=product.name,
            sku=product.sku,
            price_minor=product.price_minor,
            currency=product.currency,
            unit=product.unit,
            is_weighted=product.is_weighted,
            tax_id=product.tax_id,
            tax_rate_basis_points=tax.rate_basis_points if tax else None,
            barcodes=[b.code for b in product.barcodes],
        )


@router.get("", response_model=list[ProductOut])
def search_products(
    q: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("orders.create")),
):
    # Tenant-scoped: previously this had no tenant filter at all, so a
    # search would return every tenant's products mixed together.
    query = (
        select(Product)
        .where(Product.tenant_id == principal.tenant_id)
        .where(Product.is_deleted.is_(False))
        .where(Product.pos_visible.is_(True))
    )
    if q:
        like = f"%{q}%"
        query = query.where((Product.name.ilike(like)) | (Product.sku.ilike(like)))
    # `limit` is also used by the Electron app's offline catalog-sync pull
    # (Phase 8 rebuild) to fetch the full pos_visible catalog in one call
    # rather than being capped at 50 forever.
    products = db.execute(query.limit(min(limit, 1000))).scalars().all()
    taxes = {t.id: t for t in db.execute(select(Tax).where(Tax.tenant_id == principal.tenant_id)).scalars().all()}
    return [ProductOut.from_product(p, taxes) for p in products]


@router.get("/barcode/{code}", response_model=ProductOut)
def lookup_by_barcode(
    code: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("orders.create")),
):
    barcode = (
        db.query(Barcode)
        .filter(Barcode.code == code, Barcode.tenant_id == principal.tenant_id)
        .first()
    )
    if not barcode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No product with that barcode")
    product = db.get(Product, barcode.product_id)
    if not product or product.is_deleted or product.tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    tax = db.get(Tax, product.tax_id) if product.tax_id else None
    return ProductOut.from_product(product, {tax.id: tax} if tax else {})


class ProductCreate(BaseModel):
    name: str
    sku: str | None = None
    price_minor: int
    currency: str = "EUR"
    category_id: int | None = None
    tax_id: int | None = None
    unit: str = "piece"
    is_weighted: bool = False
    barcode: str | None = None


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(
    body: ProductCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("products.create")),
):
    product = Product(
        tenant_id=principal.tenant_id,
        name=body.name,
        sku=body.sku,
        price_minor=body.price_minor,
        currency=body.currency,
        category_id=body.category_id,
        tax_id=body.tax_id,
        unit=body.unit,
        is_weighted=body.is_weighted,
    )
    db.add(product)
    db.flush()
    if body.barcode:
        db.add(Barcode(tenant_id=principal.tenant_id, product_id=product.id, code=body.barcode))
    db.commit()
    db.refresh(product)
    tax = db.get(Tax, product.tax_id) if product.tax_id else None
    return ProductOut.from_product(product, {tax.id: tax} if tax else {})
