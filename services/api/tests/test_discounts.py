"""
Phase 9B — discount authorization gate (app/services/discounts.py).
Mirrors test_refunds.py's structure: fail-safe default, threshold-based
gating, permission checks done on the actual Principal (not a caller-
supplied boolean), and a real Approval-row workflow for the over-
threshold case — except the gate runs BEFORE any Order exists, since a
discount is decided at checkout time, not applied to a completed sale.
"""
from __future__ import annotations

import pytest

from app.core.security import Principal
from app.domain.authz import Approval, ApprovalPolicy, User
from app.domain.cash import CashierSession
from app.domain.catalog import Product, Category
from app.domain.orders import Order, OrderLine, PaymentMethod
from app.domain.seed import seed_all
from app.domain.tenancy import Register, Store
from app.services.checkout import CartLineInput
from app.services.discounts import (
    DiscountApprovalError,
    DiscountRequiresApprovalError,
    checkout_with_discount_check,
    resolve_discount_approval,
)


@pytest.fixture
def discount_setup(db):
    tenant = seed_all(db)
    cashier = User(tenant_id=tenant.id, name="Discount Cashier", email="disc-cashier@test-fixture.local", password_hash="x")
    manager = User(tenant_id=tenant.id, name="Discount Manager", email="disc-manager@test-fixture.local", password_hash="x")
    db.add(cashier)
    db.add(manager)
    category = db.query(Category).filter(Category.slug == "grocery", Category.tenant_id == tenant.id).first()
    store = db.query(Store).filter(Store.tenant_id == tenant.id).first()
    register = db.query(Register).filter(Register.store_id == store.id).first()
    product = Product(tenant_id=tenant.id, category_id=category.id, name="Flour 1kg", price_minor=1000, currency="EUR")
    db.add(product)
    db.flush()
    db.add(CashierSession(
        tenant_id=tenant.id, store_id=store.id, register_id=register.id,
        cashier_user_id=cashier.id, opening_cash_minor=10000, is_open=True,
    ))
    db.commit()
    cashier_principal = Principal(user_id=cashier.id, tenant_id=tenant.id, role="Cashier", store_id=store.id)
    manager_principal = Principal(user_id=manager.id, tenant_id=tenant.id, role="Store Manager", store_id=store.id)
    return {
        "tenant_id": tenant.id, "store_id": store.id, "register_id": register.id, "product": product,
        "cashier_id": cashier.id, "cashier_principal": cashier_principal, "manager_principal": manager_principal,
    }


def test_a_cart_with_no_discount_never_touches_the_approval_system(db, discount_setup):
    """The common case (zero discount) must be indistinguishable from a
    plain checkout — no ApprovalPolicy needed, no Approval row created."""
    order = checkout_with_discount_check(
        db, tenant_id=discount_setup["tenant_id"], store_id=discount_setup["store_id"],
        register_id=discount_setup["register_id"], cashier_user_id=discount_setup["cashier_id"],
        principal=discount_setup["cashier_principal"],
        lines=[CartLineInput(discount_setup["product"].id, 2)],
        payment_method=PaymentMethod.CASH,
    )
    assert order.discount_minor == 0
    assert order.subtotal_minor == 2000
    assert db.query(Approval).count() == 0


def test_no_policy_means_any_discount_needs_approval_fail_safe(db, discount_setup):
    """Same fail-safe-not-fail-open convention as refunds: no
    ApprovalPolicy row for 'orders.discount' -> every discount, however
    small, requires approval. A Cashier with only orders.discount.apply
    (no override) must be blocked here, not silently allowed through."""
    with pytest.raises(DiscountRequiresApprovalError) as exc_info:
        checkout_with_discount_check(
            db, tenant_id=discount_setup["tenant_id"], store_id=discount_setup["store_id"],
            register_id=discount_setup["register_id"], cashier_user_id=discount_setup["cashier_id"],
            principal=discount_setup["cashier_principal"],
            lines=[CartLineInput(discount_setup["product"].id, 2, discount_minor=100)],
            payment_method=PaymentMethod.CASH,
        )
    assert exc_info.value.approval_id is not None
    # No order was created — this is the key difference from refunds,
    # which act on an order that already exists.
    assert db.query(Order).count() == 0


def test_discount_under_threshold_applies_immediately(db, discount_setup):
    db.add(ApprovalPolicy(tenant_id=discount_setup["tenant_id"], action_code="orders.discount", threshold_minor_units=500, required_role="Store Manager"))
    db.commit()
    order = checkout_with_discount_check(
        db, tenant_id=discount_setup["tenant_id"], store_id=discount_setup["store_id"],
        register_id=discount_setup["register_id"], cashier_user_id=discount_setup["cashier_id"],
        principal=discount_setup["cashier_principal"],
        lines=[CartLineInput(discount_setup["product"].id, 2, discount_minor=100)],
        payment_method=PaymentMethod.CASH,
    )
    # 2000 subtotal - 100 discount = 1900, tax on the DISCOUNTED base
    assert order.discount_minor == 100
    assert order.subtotal_minor == 1900
    line = db.query(OrderLine).filter(OrderLine.order_id == order.id).first()
    assert line.discount_minor == 100


def test_discount_over_threshold_without_override_creates_a_real_approval(db, discount_setup):
    db.add(ApprovalPolicy(tenant_id=discount_setup["tenant_id"], action_code="orders.discount", threshold_minor_units=50, required_role="Store Manager"))
    db.commit()
    with pytest.raises(DiscountRequiresApprovalError) as exc_info:
        checkout_with_discount_check(
            db, tenant_id=discount_setup["tenant_id"], store_id=discount_setup["store_id"],
            register_id=discount_setup["register_id"], cashier_user_id=discount_setup["cashier_id"],
            principal=discount_setup["cashier_principal"],
            lines=[CartLineInput(discount_setup["product"].id, 2, discount_minor=100)],
            payment_method=PaymentMethod.CASH,
        )
    approval = db.get(Approval, exc_info.value.approval_id)
    assert approval.status == "REQUESTED"
    assert approval.action_code == "orders.discount"
    assert approval.context["total_discount_minor"] == 100
    assert db.query(Order).count() == 0  # nothing was created yet


def test_manager_resolving_approval_actually_creates_the_order(db, discount_setup):
    db.add(ApprovalPolicy(tenant_id=discount_setup["tenant_id"], action_code="orders.discount", threshold_minor_units=50, required_role="Store Manager"))
    db.commit()
    with pytest.raises(DiscountRequiresApprovalError) as exc_info:
        checkout_with_discount_check(
            db, tenant_id=discount_setup["tenant_id"], store_id=discount_setup["store_id"],
            register_id=discount_setup["register_id"], cashier_user_id=discount_setup["cashier_id"],
            principal=discount_setup["cashier_principal"],
            lines=[CartLineInput(discount_setup["product"].id, 2, discount_minor=100)],
            payment_method=PaymentMethod.CASH,
        )
    approval_id = exc_info.value.approval_id

    approval, order = resolve_discount_approval(
        db, tenant_id=discount_setup["tenant_id"], approval_id=approval_id,
        resolver=discount_setup["manager_principal"], approve=True,
    )
    assert approval.status == "APPROVED"
    assert order is not None
    assert order.discount_minor == 100
    assert order.subtotal_minor == 1900


def test_manager_rejecting_approval_creates_no_order(db, discount_setup):
    db.add(ApprovalPolicy(tenant_id=discount_setup["tenant_id"], action_code="orders.discount", threshold_minor_units=50, required_role="Store Manager"))
    db.commit()
    with pytest.raises(DiscountRequiresApprovalError) as exc_info:
        checkout_with_discount_check(
            db, tenant_id=discount_setup["tenant_id"], store_id=discount_setup["store_id"],
            register_id=discount_setup["register_id"], cashier_user_id=discount_setup["cashier_id"],
            principal=discount_setup["cashier_principal"],
            lines=[CartLineInput(discount_setup["product"].id, 2, discount_minor=100)],
            payment_method=PaymentMethod.CASH,
        )
    approval, order = resolve_discount_approval(
        db, tenant_id=discount_setup["tenant_id"], approval_id=exc_info.value.approval_id,
        resolver=discount_setup["manager_principal"], approve=False,
    )
    assert approval.status == "REJECTED"
    assert order is None
    assert db.query(Order).count() == 0


def test_cashier_cannot_resolve_their_own_discount_approval(db, discount_setup):
    """Same discipline as refunds' test_cashier_cannot_resolve_their_own_approval
    — resolving is independently re-checked against the RESOLVER's own
    permissions, not trusted from the route layer."""
    db.add(ApprovalPolicy(tenant_id=discount_setup["tenant_id"], action_code="orders.discount", threshold_minor_units=50, required_role="Store Manager"))
    db.commit()
    with pytest.raises(DiscountRequiresApprovalError) as exc_info:
        checkout_with_discount_check(
            db, tenant_id=discount_setup["tenant_id"], store_id=discount_setup["store_id"],
            register_id=discount_setup["register_id"], cashier_user_id=discount_setup["cashier_id"],
            principal=discount_setup["cashier_principal"],
            lines=[CartLineInput(discount_setup["product"].id, 2, discount_minor=100)],
            payment_method=PaymentMethod.CASH,
        )
    with pytest.raises(DiscountApprovalError):
        resolve_discount_approval(
            db, tenant_id=discount_setup["tenant_id"], approval_id=exc_info.value.approval_id,
            resolver=discount_setup["cashier_principal"], approve=True,
        )


def test_manager_override_holder_applies_over_threshold_discount_directly(db, discount_setup):
    """A Store Manager (holds orders.discount.override) applying a
    discount themselves at the register needs no separate approval step
    — this IS the override, happening synchronously."""
    db.add(ApprovalPolicy(tenant_id=discount_setup["tenant_id"], action_code="orders.discount", threshold_minor_units=50, required_role="Store Manager"))
    db.commit()
    order = checkout_with_discount_check(
        db, tenant_id=discount_setup["tenant_id"], store_id=discount_setup["store_id"],
        register_id=discount_setup["register_id"], cashier_user_id=discount_setup["cashier_id"],
        principal=discount_setup["manager_principal"],
        lines=[CartLineInput(discount_setup["product"].id, 2, discount_minor=100)],
        payment_method=PaymentMethod.CASH,
    )
    assert order.discount_minor == 100
    assert db.query(Approval).count() == 0


def test_discount_larger_than_the_line_clamps_to_zero_not_negative(db, discount_setup):
    """A discount exceeding a line's own subtotal must never produce a
    negative subtotal/tax (that would be a 'sale' that pays the
    customer) — it clamps to zero and the ACTUAL applied amount (not the
    requested one) is what's recorded."""
    order = checkout_with_discount_check(
        db, tenant_id=discount_setup["tenant_id"], store_id=discount_setup["store_id"],
        register_id=discount_setup["register_id"], cashier_user_id=discount_setup["cashier_id"],
        principal=discount_setup["manager_principal"],  # has override, no approval needed regardless
        lines=[CartLineInput(discount_setup["product"].id, 1, discount_minor=999999)],
        payment_method=PaymentMethod.CASH,
    )
    assert order.subtotal_minor == 0
    assert order.tax_minor == 0
    assert order.discount_minor == 1000  # clamped to the line's own subtotal, not the requested 999999
