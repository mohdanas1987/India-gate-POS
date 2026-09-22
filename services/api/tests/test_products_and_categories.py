"""
Phase 9A — tests for the category sidebar's backend: the new
GET /api/v1/categories endpoint, and the extensions to product search
(category_id filter, barcode matching) the CTO's gap analysis found
entirely missing before this pass.
"""
from __future__ import annotations

import pytest

from app.api.v1.categories import list_categories
from app.api.v1.products import get_product, search_products
from app.core.security import Principal
from app.domain.authz import User
from app.domain.catalog import Barcode, Category, Product, Tax
from app.domain.seed import seed_all


@pytest.fixture
def catalog_setup(db):
    tenant = seed_all(db)
    user = User(tenant_id=tenant.id, name="Cat Test Cashier", email="cat-cashier@test-fixture.local", password_hash="x")
    db.add(user)
    tax = Tax(tenant_id=tenant.id, name="Standard", rate_basis_points=2100)
    db.add(tax)
    db.flush()

    grocery = db.query(Category).filter(Category.slug == "grocery", Category.tenant_id == tenant.id).first()
    beverages = db.query(Category).filter(Category.slug == "beverages", Category.tenant_id == tenant.id).first()

    rice = Product(
        tenant_id=tenant.id, category_id=grocery.id, tax_id=tax.id, sku="RICE-1KG",
        name="Basmati Rice 1kg", price_minor=299, currency="EUR", unit="piece",
    )
    cola = Product(
        tenant_id=tenant.id, category_id=beverages.id, tax_id=tax.id, sku="COLA-500ML",
        name="Cola 500ml", price_minor=150, currency="EUR", unit="piece",
    )
    db.add_all([rice, cola])
    db.flush()
    db.add(Barcode(tenant_id=tenant.id, product_id=cola.id, code="8901234567890"))
    db.commit()

    principal = Principal(user_id=user.id, tenant_id=tenant.id, role="Cashier", store_id=None)
    return {"principal": principal, "grocery": grocery, "beverages": beverages, "rice": rice, "cola": cola}


def test_list_categories_returns_pos_visible_product_counts(db, catalog_setup):
    result = list_categories(db, catalog_setup["principal"])
    by_slug = {c.slug: c for c in result}

    assert by_slug["grocery"].product_count == 1
    assert by_slug["beverages"].product_count == 1
    # "Extra" is seeded with sync_to_website=False but is still an active,
    # POS-sellable category — it has zero products in this fixture, and
    # should still be listed (a category with 0 products is not the same
    # as an inactive category, which must not be listed at all).
    assert "extra" in by_slug
    assert by_slug["extra"].product_count == 0


def test_category_filter_narrows_product_search(db, catalog_setup):
    grocery_results = search_products(
        q=None, category_id=catalog_setup["grocery"].id, limit=50, db=db, principal=catalog_setup["principal"]
    )
    assert [p.id for p in grocery_results] == [catalog_setup["rice"].id]

    beverages_results = search_products(
        q=None, category_id=catalog_setup["beverages"].id, limit=50, db=db, principal=catalog_setup["principal"]
    )
    assert [p.id for p in beverages_results] == [catalog_setup["cola"].id]


def test_search_matches_a_barcode_not_just_name_or_sku(db, catalog_setup):
    """Phase 9A gap analysis finding: the search endpoint only ever
    matched name/SKU — a keyboard-wedge barcode scanner injecting a
    barcode into the same search box got zero results. This proves the
    fix without going through the separate, still-present exact-lookup
    /products/barcode/{code} route."""
    results = search_products(q="8901234567890", category_id=None, limit=50, db=db, principal=catalog_setup["principal"])
    assert [p.id for p in results] == [catalog_setup["cola"].id]


def test_search_does_not_duplicate_a_product_with_multiple_barcodes(db, catalog_setup):
    db.add(Barcode(tenant_id=catalog_setup["principal"].tenant_id, product_id=catalog_setup["cola"].id, code="ALT-CODE-1"))
    db.commit()

    results = search_products(q="Cola", category_id=None, limit=50, db=db, principal=catalog_setup["principal"])
    assert len(results) == 1
    assert results[0].id == catalog_setup["cola"].id


def test_product_out_carries_category_id(db, catalog_setup):
    results = search_products(q="Rice", category_id=None, limit=50, db=db, principal=catalog_setup["principal"])
    assert results[0].category_id == catalog_setup["grocery"].id


def test_get_product_by_id_returns_the_right_product(db, catalog_setup):
    """Phase 9B: recalling a held cart needs to rebuild full line detail
    from just a product_id — this is that lookup."""
    result = get_product(catalog_setup["rice"].id, db=db, principal=catalog_setup["principal"])
    assert result.id == catalog_setup["rice"].id
    assert result.name == "Basmati Rice 1kg"


def test_get_product_by_id_is_tenant_scoped(db, catalog_setup):
    import pytest as _pytest
    from fastapi import HTTPException

    from app.domain.seed import seed_all as _seed_all
    from app.domain.tenancy import Tenant

    other_tenant = Tenant(name="Other Tenant For Product Lookup")
    db.add(other_tenant)
    db.flush()
    from app.domain.catalog import Product as _Product

    other_product = _Product(tenant_id=other_tenant.id, name="Cross Tenant Product", price_minor=100, currency="EUR")
    db.add(other_product)
    db.commit()

    with _pytest.raises(HTTPException) as exc_info:
        get_product(other_product.id, db=db, principal=catalog_setup["principal"])
    assert exc_info.value.status_code == 404


def test_cursor_pagination_pages_through_the_full_catalog_with_no_gaps_or_duplicates(db, catalog_setup):
    """
    Phase 9A correction gate (CTO review of d6bad7c, finding #8): the
    Electron offline catalog sync used to do one `?limit=1000` request and
    simply could not retrieve a catalog bigger than that — India Gate's
    real dataset has ~6,843 products. This proves the fix at the layer
    the bug actually lived in: the search endpoint's own pagination
    contract, independent of Electron. Seeds enough extra products that a
    small page size forces multiple pages, then walks the `cursor` the
    same way the Electron sync loop does (last id of the previous page,
    stop on a short page) and asserts every product is returned exactly
    once, in ascending id order, matching the actual seeded set.
    """
    tenant_id = catalog_setup["principal"].tenant_id
    tax = db.query(Tax).filter(Tax.tenant_id == tenant_id).first()
    grocery = catalog_setup["grocery"]
    extra_products = [
        Product(
            tenant_id=tenant_id, category_id=grocery.id, tax_id=tax.id, sku=f"PG-{i}",
            name=f"Pagination Test Product {i}", price_minor=100 + i, currency="EUR", unit="piece",
        )
        for i in range(7)
    ]
    db.add_all(extra_products)
    db.commit()

    all_seeded_ids = sorted([catalog_setup["rice"].id, catalog_setup["cola"].id] + [p.id for p in extra_products])

    collected: list[int] = []
    cursor = None
    page_size = 3
    pages_fetched = 0
    while True:
        pages_fetched += 1
        assert pages_fetched <= 20, "pagination loop did not terminate — cursor is not advancing"
        page = search_products(
            q=None, category_id=None, limit=page_size, cursor=cursor, db=db, principal=catalog_setup["principal"]
        )
        if not page:
            break
        collected.extend(p.id for p in page)
        cursor = page[-1].id
        if len(page) < page_size:
            break

    assert pages_fetched > 1, "test fixture didn't actually force multiple pages"
    assert collected == all_seeded_ids, "cursor pagination must return every product exactly once, in id order"
