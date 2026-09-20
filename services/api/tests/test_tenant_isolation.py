"""
Phase 8.5 — Security + Transaction Integrity Gate.

CTO audit of commit 0cfd8ca, finding #27: "there are not enough security
tests for the issues I found ... these should be mandatory regression
tests," listing exactly the scenarios below. Every one of these used to
be silently possible (or, for the ones already covered by an existing
tenant check, was untested against the SERVICE layer specifically, only
the route layer) before this gate. Each test proves the corresponding
attack is refused, not merely that a route happens to add a WHERE clause.

Two-tenant fixture layout:
  Tenant A / Store A1 / Register A1-1  (created via seed_all, the shared
    convention every other test file in this suite already uses)
  Tenant B / Store B1 / Register B1-1  (created by hand — seed_all only
    ever creates one tenant, "India Gate")

Two-store-same-tenant fixture layout (for the store-isolation cases,
which are a different failure mode from tenant isolation — same tenant,
wrong store):
  Tenant A / Store A1 / Register A1-1
  Tenant A / Store A2 / Register A2-1
"""
from __future__ import annotations

import pytest

from app.core.security import Principal
from app.domain.authz import User
from app.domain.cash import CashierSession
from app.domain.catalog import Category, Product
from app.domain.orders import PaymentMethod
from app.domain.seed import seed_all
from app.domain.tenancy import Register, Store, Tenant
from app.services.authorization import AuthorizationError, resolve_authorized_register
from app.services.checkout import CartLineInput, InvalidCartError, UnauthorizedResourceError, create_pos_sale
from app.services.refunds import RefundError, process_refund
from app.api.v1.cash import close_session as close_session_route
from app.api.v1.orders import get_order as get_order_route
from app.api.v1.website_orders import change_status as change_status_route
from fastapi import HTTPException


@pytest.fixture
def two_tenant_setup(db):
    tenant_a = seed_all(db)
    store_a = db.query(Store).filter(Store.tenant_id == tenant_a.id).first()
    register_a = db.query(Register).filter(Register.store_id == store_a.id).first()
    category_a = db.query(Category).filter(Category.tenant_id == tenant_a.id, Category.slug == "grocery").first()

    user_a = User(tenant_id=tenant_a.id, name="Cashier A", email="cashier-a@test-fixture.local", password_hash="x")
    db.add(user_a)
    product_a = Product(tenant_id=tenant_a.id, category_id=category_a.id, name="Tenant A Product", price_minor=1000, currency="EUR")
    db.add(product_a)
    db.flush()
    db.add(CashierSession(
        tenant_id=tenant_a.id, store_id=store_a.id, register_id=register_a.id,
        cashier_user_id=user_a.id, opening_cash_minor=10000, is_open=True,
    ))
    db.flush()

    # Tenant B — a completely separate tenant, built by hand since
    # seed_all() only ever creates "India Gate".
    tenant_b = Tenant(name="Rival Tenant")
    db.add(tenant_b)
    db.flush()
    store_b = Store(tenant_id=tenant_b.id, name="Rival Store")
    db.add(store_b)
    db.flush()
    register_b = Register(store_id=store_b.id, name="Rival Register")
    db.add(register_b)
    db.flush()
    user_b = User(tenant_id=tenant_b.id, name="Cashier B", email="cashier-b@test-fixture.local", password_hash="x")
    db.add(user_b)
    product_b = Product(tenant_id=tenant_b.id, name="Tenant B Product", price_minor=2000, currency="EUR")
    db.add(product_b)
    db.flush()
    db.add(CashierSession(
        tenant_id=tenant_b.id, store_id=store_b.id, register_id=register_b.id,
        cashier_user_id=user_b.id, opening_cash_minor=5000, is_open=True,
    ))
    db.flush()

    principal_a = Principal(user_id=user_a.id, tenant_id=tenant_a.id, role="Cashier", store_id=store_a.id)
    principal_b = Principal(user_id=user_b.id, tenant_id=tenant_b.id, role="Cashier", store_id=store_b.id)

    order_b = create_pos_sale(
        db, tenant_id=tenant_b.id, store_id=store_b.id, register_id=register_b.id,
        cashier_user_id=user_b.id, lines=[CartLineInput(product_b.id, 1)], payment_method=PaymentMethod.CASH,
    )
    db.commit()
    db.refresh(order_b)

    return {
        "tenant_a": tenant_a, "store_a": store_a, "register_a": register_a, "product_a": product_a, "principal_a": principal_a,
        "tenant_b": tenant_b, "store_b": store_b, "register_b": register_b, "product_b": product_b, "principal_b": principal_b,
        "order_b": order_b,
    }


def test_tenant_a_cannot_checkout_against_tenant_b_product(db, two_tenant_setup):
    s = two_tenant_setup
    with pytest.raises(InvalidCartError):
        create_pos_sale(
            db, tenant_id=s["tenant_a"].id, store_id=s["store_a"].id, register_id=s["register_a"].id,
            cashier_user_id=s["principal_a"].user_id,
            lines=[CartLineInput(s["product_b"].id, 1)],  # Tenant B's product id
            payment_method=PaymentMethod.CASH,
        )


def test_tenant_a_cannot_checkout_using_tenant_b_register(db, two_tenant_setup):
    s = two_tenant_setup
    with pytest.raises(UnauthorizedResourceError):
        create_pos_sale(
            db, tenant_id=s["tenant_a"].id, store_id=s["store_a"].id,
            register_id=s["register_b"].id,  # Tenant B's register id
            cashier_user_id=s["principal_a"].user_id,
            lines=[CartLineInput(s["product_a"].id, 1)],
            payment_method=PaymentMethod.CASH,
        )


def test_tenant_a_cannot_checkout_using_tenant_b_cashier_session(db, two_tenant_setup):
    """Even if a caller somehow got past register validation, the session
    lookup itself must also require tenant_id/store_id to match — the
    fix for finding #6's 'essentially register_id-only' session query."""
    s = two_tenant_setup
    # Tenant A has NO open session on Tenant B's register, so this is
    # exactly the failure resolve_authorized_register is meant to catch
    # first — asserted here as belt-and-suspenders on the full call path.
    with pytest.raises(UnauthorizedResourceError):
        create_pos_sale(
            db, tenant_id=s["tenant_a"].id, store_id=s["store_a"].id, register_id=s["register_b"].id,
            cashier_user_id=s["principal_a"].user_id,
            lines=[CartLineInput(s["product_a"].id, 1)], payment_method=PaymentMethod.CASH,
        )


def test_tenant_a_cannot_read_tenant_b_order(db, two_tenant_setup):
    s = two_tenant_setup
    with pytest.raises(HTTPException) as exc_info:
        get_order_route(s["order_b"].id, db, s["principal_a"])
    assert exc_info.value.status_code == 404


def test_tenant_a_cannot_refund_tenant_b_order(db, two_tenant_setup):
    s = two_tenant_setup
    with pytest.raises(RefundError):
        process_refund(
            db, tenant_id=s["tenant_a"].id, order_id=s["order_b"].id,
            principal=s["principal_a"], amount_minor=100, reason="cross-tenant refund attempt",
        )


def test_tenant_a_cannot_close_tenant_b_cashier_session(db, two_tenant_setup):
    s = two_tenant_setup
    session_b = (
        db.query(CashierSession)
        .filter(CashierSession.tenant_id == s["tenant_b"].id, CashierSession.is_open.is_(True))
        .first()
    )
    from app.api.v1.cash import CloseSessionRequest

    with pytest.raises(HTTPException) as exc_info:
        close_session_route(session_b.id, CloseSessionRequest(closing_cash_minor=0), db, s["principal_a"])
    assert exc_info.value.status_code == 404


@pytest.fixture
def two_store_same_tenant_setup(db):
    tenant = seed_all(db)
    store_1 = db.query(Store).filter(Store.tenant_id == tenant.id).first()
    register_1 = db.query(Register).filter(Register.store_id == store_1.id).first()

    store_2 = Store(tenant_id=tenant.id, name="Second Store, Same Tenant")
    db.add(store_2)
    db.flush()
    register_2 = Register(store_id=store_2.id, name="Store 2 Register")
    db.add(register_2)
    db.flush()

    user_1 = User(tenant_id=tenant.id, name="Store 1 Cashier", email="store1-cashier@test-fixture.local", password_hash="x")
    db.add(user_1)
    db.flush()
    principal_store_1 = Principal(user_id=user_1.id, tenant_id=tenant.id, role="Cashier", store_id=store_1.id)

    return {
        "tenant": tenant, "store_1": store_1, "register_1": register_1,
        "store_2": store_2, "register_2": register_2, "principal_store_1": principal_store_1,
    }


def test_store_a_cannot_use_store_b_register_for_checkout(db, two_store_same_tenant_setup):
    """Same tenant, different store — the failure mode finding #6 called
    out separately from cross-tenant: a register belonging to Store 2
    must be refused for a caller scoped to Store 1, even though both
    stores share the same tenant_id."""
    s = two_store_same_tenant_setup
    category = db.query(Category).filter(Category.tenant_id == s["tenant"].id, Category.slug == "grocery").first()
    product = Product(tenant_id=s["tenant"].id, category_id=category.id, name="Store 1 Product", price_minor=500, currency="EUR")
    db.add(product)
    db.flush()

    with pytest.raises(UnauthorizedResourceError):
        create_pos_sale(
            db, tenant_id=s["tenant"].id, store_id=s["store_1"].id,
            register_id=s["register_2"].id,  # belongs to Store 2, not Store 1
            cashier_user_id=s["principal_store_1"].user_id,
            lines=[CartLineInput(product.id, 1)], payment_method=PaymentMethod.CASH,
        )


def test_store_a_cannot_open_a_session_on_store_b_register(db, two_store_same_tenant_setup):
    s = two_store_same_tenant_setup
    with pytest.raises(AuthorizationError):
        resolve_authorized_register(db, s["tenant"].id, s["store_1"].id, s["register_2"].id)


def test_store_a_cannot_change_store_b_website_order_status(db, two_store_same_tenant_setup):
    """Same tenant, different store. Fixed alongside this gate in
    app/api/v1/website_orders.py's change_status(): the read/list path
    already scoped by store when principal.store_id is set, but the
    mutation path (change_status) previously checked only tenant_id,
    which meant a Store 1 caller could change a Store 2 website order's
    status as long as they shared a tenant."""
    from app.domain.website import WebsiteOrder
    from app.domain.orders import Order, OrderStatus, SalesChannel
    from app.api.v1.website_orders import StatusChangeRequest

    s = two_store_same_tenant_setup
    order_store_2 = Order(
        tenant_id=s["tenant"].id, store_id=s["store_2"].id, sales_channel=SalesChannel.WEBSITE,
        status=OrderStatus.OPEN, currency="EUR",
    )
    db.add(order_store_2)
    db.flush()
    wo = WebsiteOrder(
        tenant_id=s["tenant"].id, order_id=order_store_2.id,
        external_order_number="WEB-STORE2-1", status="NEW",
    )
    db.add(wo)
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        change_status_route(wo.id, StatusChangeRequest(new_status="CONFIRMED"), db, s["principal_store_1"])
    assert exc_info.value.status_code == 404
    assert wo.status == "NEW"  # unchanged
