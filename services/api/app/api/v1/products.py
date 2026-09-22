"""Minimal product API needed to build a cart (Phase 5 API layer, the
part flagged missing in the phase-status report). Full CRUD/import is
still a follow-up — this is search + barcode lookup + a basic create,
enough for checkout (Phase 6/7) to have something real to sell."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import or_, select
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
    # Phase 9A: the category sidebar needs to know which category a
    # product belongs to once it's returned from a category-filtered (or
    # unfiltered) search — previously absent from this contract even
    # though `Product.category_id` has existed since Phase 3.
    category_id: int | None = None
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
            category_id=product.category_id,
            tax_rate_basis_points=tax.rate_basis_points if tax else None,
            barcodes=[b.code for b in product.barcodes],
        )


@router.get("", response_model=list[ProductOut])
def search_products(
    q: str | None = None,
    category_id: int | None = None,
    limit: int = 50,
    cursor: int | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("orders.create")),
):
    """
    `cursor` (Phase 9A correction gate — CTO review of d6bad7c, finding
    #8): the previous offline catalog-sync pull did `?limit=1000` in one
    shot and simply could not retrieve a catalog bigger than that (India
    Gate's real dataset has ~6,843 products). Rather than raise the cap
    arbitrarily (still a scaling wall) or build a second parallel sync
    mechanism, this adds standard id-keyset pagination to the existing
    search endpoint: pass the highest `id` seen in the previous page as
    `cursor` to get the next page, ordered by `Product.id` ascending so
    pages never overlap or skip a row even if products are inserted
    between calls (unlike OFFSET pagination, which can). An empty result
    (or a page shorter than `limit`) means the caller has reached the
    end. `q`/`category_id` searches are unaffected and typically don't
    need to paginate at all — this exists for the full-catalog pull.
    """
    # Tenant-scoped: previously this had no tenant filter at all, so a
    # search would return every tenant's products mixed together.
    query = (
        select(Product)
        .where(Product.tenant_id == principal.tenant_id)
        .where(Product.is_deleted.is_(False))
        .where(Product.pos_visible.is_(True))
    )
    if cursor is not None:
        query = query.where(Product.id > cursor)
    if q:
        like = f"%{q}%"
        # Phase 9A: the gap analysis found this only ever matched
        # name/SKU — a cashier typing (or a keyboard-wedge scanner
        # injecting) a barcode into the same search box got zero results
        # and had to know to hit the separate /products/barcode/{code}
        # lookup instead. A search-time outerjoin against Barcode closes
        # that gap without touching the dedicated exact-lookup route,
        # which stays for the "we already know this is a barcode" case.
        # distinct() guards against a product with multiple barcodes
        # coming back more than once for one query.
        query = (
            query.outerjoin(Barcode, Barcode.product_id == Product.id)
            .where(or_(Product.name.ilike(like), Product.sku.ilike(like), Barcode.code.ilike(like)))
            .distinct()
        )
    if category_id is not None:
        # Phase 9A category sidebar: filter to one category at a time.
        # No tenant cross-check needed on category_id itself beyond this
        # — a category from another tenant simply matches zero products
        # because Product.category_id + Product.tenant_id are filtered
        # together, so it fails closed rather than needing a separate
        # resolve-and-403 step for a read-only filter.
        query = query.where(Product.category_id == category_id)
    # `limit` is also used by the Electron app's offline catalog-sync pull
    # (Phase 8 rebuild, now paginated via `cursor` above) to page through
    # the full pos_visible catalog rather than being capped at one fixed
    # page forever. Ordered by id so keyset pagination is stable.
    products = db.execute(query.order_by(Product.id).limit(min(limit, 1000))).scalars().all()
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
