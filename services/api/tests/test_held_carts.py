"""Phase 9B — suspended/held carts (HOLD/RECALL), tested at the route
function level (same pattern test_products_and_categories.py uses for
its endpoints — calling the FastAPI route function directly with a real
db session and Principal, not spinning up a test client)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.v1.held_carts import HeldCartLineIn, HoldCartRequest, hold_cart, list_held_carts, recall_held_cart
from app.core.security import Principal
from app.domain.authz import User
from app.domain.catalog import Category, Product
from app.domain.customer import Customer
from app.domain.held_cart import HeldCart
from app.domain.seed import seed_all
from app.domain.tenancy import Register, Store, Tenant


@pytest.fixture
def held_cart_setup(db):
    tenant = seed_all(db)
    user = User(tenant_id=tenant.id, name="Hold Cashier", email="hold-cashier@test-fixture.local", password_hash="x")
    db.add(user)
    category = db.query(Category).filter(Category.slug == "grocery", Category.tenant_id == tenant.id).first()
    store = db.query(Store).filter(Store.tenant_id == tenant.id).first()
    register = db.query(Register).filter(Register.store_id == store.id).first()
    product = Product(tenant_id=tenant.id, category_id=category.id, name="Sugar 1kg", price_minor=250, currency="EUR")
    db.add(product)
    db.flush()
    db.commit()
    principal = Principal(user_id=user.id, tenant_id=tenant.id, role="Cashier", store_id=store.id)
    return {"principal": principal, "register_id": register.id, "product": product, "store_id": store.id}


def test_holding_a_cart_creates_a_row_and_returns_its_lines(db, held_cart_setup):
    result = hold_cart(
        HoldCartRequest(
            register_id=held_cart_setup["register_id"], label="Customer forgot wallet",
            lines=[HeldCartLineIn(product_id=held_cart_setup["product"].id, quantity=3, discount_minor=0)],
        ),
        db=db, principal=held_cart_setup["principal"],
    )
    assert result.label == "Customer forgot wallet"
    assert result.lines[0].product_id == held_cart_setup["product"].id
    assert result.lines[0].quantity == 3
    assert db.query(HeldCart).count() == 1


def test_cannot_hold_an_empty_cart(db, held_cart_setup):
    with pytest.raises(HTTPException) as exc_info:
        hold_cart(
            HoldCartRequest(register_id=held_cart_setup["register_id"], lines=[]),
            db=db, principal=held_cart_setup["principal"],
        )
    assert exc_info.value.status_code == 400


def test_recall_returns_the_lines_and_deletes_the_row(db, held_cart_setup):
    held = hold_cart(
        HoldCartRequest(
            register_id=held_cart_setup["register_id"],
            lines=[HeldCartLineIn(product_id=held_cart_setup["product"].id, quantity=2)],
        ),
        db=db, principal=held_cart_setup["principal"],
    )
    recalled = recall_held_cart(held.id, db=db, principal=held_cart_setup["principal"])
    assert recalled.id == held.id
    assert recalled.lines[0].quantity == 2
    assert db.query(HeldCart).count() == 0  # recall consumes it


def test_recalling_twice_fails_the_second_time(db, held_cart_setup):
    """Proves recall is one-shot: a held cart cannot be rung up twice by
    recalling it on two terminals."""
    held = hold_cart(
        HoldCartRequest(
            register_id=held_cart_setup["register_id"],
            lines=[HeldCartLineIn(product_id=held_cart_setup["product"].id, quantity=1)],
        ),
        db=db, principal=held_cart_setup["principal"],
    )
    recall_held_cart(held.id, db=db, principal=held_cart_setup["principal"])
    with pytest.raises(HTTPException) as exc_info:
        recall_held_cart(held.id, db=db, principal=held_cart_setup["principal"])
    assert exc_info.value.status_code == 404


def test_a_store_scoped_principal_from_another_store_cannot_see_or_recall_it(db, held_cart_setup):
    tenant_id = held_cart_setup["principal"].tenant_id
    from app.domain.tenancy import Store as StoreModel

    other_store = StoreModel(tenant_id=tenant_id, name="Other Store")
    db.add(other_store)
    db.flush()
    other_user = User(tenant_id=tenant_id, name="Other Store Cashier", email="other-store-cashier@test-fixture.local", password_hash="x")
    db.add(other_user)
    db.flush()
    db.commit()
    other_principal = Principal(user_id=other_user.id, tenant_id=tenant_id, role="Cashier", store_id=other_store.id)

    held = hold_cart(
        HoldCartRequest(
            register_id=held_cart_setup["register_id"],
            lines=[HeldCartLineIn(product_id=held_cart_setup["product"].id, quantity=1)],
        ),
        db=db, principal=held_cart_setup["principal"],
    )

    other_store_list = list_held_carts(db=db, principal=other_principal)
    assert held.id not in [h.id for h in other_store_list]

    with pytest.raises(HTTPException) as exc_info:
        recall_held_cart(held.id, db=db, principal=other_principal)
    assert exc_info.value.status_code == 404


# --- Phase 9B correction gate (CTO review of 96f6aa9, security issue #1):
# hold_cart must prove every id it's about to persist belongs to the
# caller's own tenant, not just the register. ---


def test_holding_a_cart_with_another_tenants_product_is_rejected(db, held_cart_setup):
    other_tenant = Tenant(name="A Rival Grocer")
    db.add(other_tenant)
    db.flush()
    other_category = Category(tenant_id=other_tenant.id, name="Other", slug="other", sync_to_website=False)
    db.add(other_category)
    db.flush()
    foreign_product = Product(tenant_id=other_tenant.id, category_id=other_category.id, name="Foreign Rice", price_minor=500, currency="EUR")
    db.add(foreign_product)
    db.flush()
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        hold_cart(
            HoldCartRequest(
                register_id=held_cart_setup["register_id"],
                lines=[HeldCartLineIn(product_id=foreign_product.id, quantity=1)],
            ),
            db=db, principal=held_cart_setup["principal"],
        )
    assert exc_info.value.status_code == 403
    assert db.query(HeldCart).count() == 0  # rejected before anything was written


def test_holding_a_cart_with_another_tenants_customer_is_rejected(db, held_cart_setup):
    other_tenant = Tenant(name="A Rival Grocer")
    db.add(other_tenant)
    db.flush()
    foreign_customer = Customer(tenant_id=other_tenant.id, name="Not Our Customer")
    db.add(foreign_customer)
    db.flush()
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        hold_cart(
            HoldCartRequest(
                register_id=held_cart_setup["register_id"], customer_id=foreign_customer.id,
                lines=[HeldCartLineIn(product_id=held_cart_setup["product"].id, quantity=1)],
            ),
            db=db, principal=held_cart_setup["principal"],
        )
    assert exc_info.value.status_code == 403
    assert db.query(HeldCart).count() == 0


def test_holding_a_cart_with_a_nonexistent_product_id_is_rejected(db, held_cart_setup):
    with pytest.raises(HTTPException) as exc_info:
        hold_cart(
            HoldCartRequest(
                register_id=held_cart_setup["register_id"],
                lines=[HeldCartLineIn(product_id=999999, quantity=1)],
            ),
            db=db, principal=held_cart_setup["principal"],
        )
    assert exc_info.value.status_code == 403


def test_holding_a_cart_with_a_soft_deleted_product_is_rejected(db, held_cart_setup):
    held_cart_setup["product"].is_deleted = True
    db.commit()
    with pytest.raises(HTTPException) as exc_info:
        hold_cart(
            HoldCartRequest(
                register_id=held_cart_setup["register_id"],
                lines=[HeldCartLineIn(product_id=held_cart_setup["product"].id, quantity=1)],
            ),
            db=db, principal=held_cart_setup["principal"],
        )
    assert exc_info.value.status_code == 403


def test_holding_a_cart_with_a_non_positive_quantity_is_rejected(db, held_cart_setup):
    with pytest.raises(HTTPException) as exc_info:
        hold_cart(
            HoldCartRequest(
                register_id=held_cart_setup["register_id"],
                lines=[HeldCartLineIn(product_id=held_cart_setup["product"].id, quantity=0)],
            ),
            db=db, principal=held_cart_setup["principal"],
        )
    assert exc_info.value.status_code == 403


def test_holding_a_cart_with_a_negative_discount_is_rejected(db, held_cart_setup):
    with pytest.raises(HTTPException) as exc_info:
        hold_cart(
            HoldCartRequest(
                register_id=held_cart_setup["register_id"],
                lines=[HeldCartLineIn(product_id=held_cart_setup["product"].id, quantity=1, discount_minor=-50)],
            ),
            db=db, principal=held_cart_setup["principal"],
        )
    assert exc_info.value.status_code == 403
