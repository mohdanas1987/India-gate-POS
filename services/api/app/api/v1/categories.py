"""
Phase 9A — category navigation. The `Category` domain model has existed
since Phase 3 (used by product-sync's "Extra" exclusion rule), but no API
route ever exposed it — the CTO's Phase 9A gap analysis found the POS UI
had no category sidebar at all and nothing to build one from. This is
that missing read endpoint: tenant-scoped, POS-visible product counts per
category, active categories only (an inactive category shouldn't appear
as a navigable tab even if old products still reference it).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.rbac import require_permission
from app.core.security import Principal
from app.db.session import get_db
from app.domain.catalog import Category, Product

router = APIRouter(prefix="/categories", tags=["categories"])


class CategoryOut(BaseModel):
    id: int
    name: str
    slug: str
    product_count: int

    class Config:
        from_attributes = True


@router.get("", response_model=list[CategoryOut])
def list_categories(
    db: Session = Depends(get_db),
    # Gated on the same permission product search uses (orders.create) —
    # a cashier building a cart needs to browse categories, and there is
    # no separate "categories.view" permission in this codebase's RBAC
    # model (see app/domain/seed.py) worth inventing for a read-only
    # navigation list.
    principal: Principal = Depends(require_permission("orders.create")),
):
    # One query for categories, one aggregate query for counts — avoids
    # an N+1 COUNT(*) per category for a tenant with many categories.
    # The count only reflects POS-sellable products: not deleted, and
    # pos_visible, matching exactly what search_products() itself returns
    # — a category showing "12" should mean 12 products actually
    # reachable by browsing into it, not 12 minus some hidden subset.
    categories = (
        db.execute(
            select(Category)
            .where(Category.tenant_id == principal.tenant_id, Category.is_active.is_(True))
            .order_by(Category.name)
        )
        .scalars()
        .all()
    )

    count_rows = db.execute(
        select(Product.category_id, func.count(Product.id))
        .where(
            Product.tenant_id == principal.tenant_id,
            Product.is_deleted.is_(False),
            Product.pos_visible.is_(True),
        )
        .group_by(Product.category_id)
    ).all()
    counts_by_category = {category_id: count for category_id, count in count_rows}

    return [
        CategoryOut(id=c.id, name=c.name, slug=c.slug, product_count=counts_by_category.get(c.id, 0))
        for c in categories
    ]
