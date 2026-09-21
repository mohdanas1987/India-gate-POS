"""
Phase 22 (LedgerBrug dev-team question #4, second half): void, distinct
from refund. See app/services/voids.py for the full rationale.
"""
import datetime as dt

import pytest

from app.core.rbac import principal_has_permission
from app.core.security import Principal
from app.domain.authz import User, UserRole, Role
from app.domain.cash import CashierSession
from app.domain.catalog import Product, Tax, Category
from app.domain.inventory import InventoryLedger, LedgerEventType
from app.domain.orders import Order, OrderStatus, PaymentMethod
from app.domain.seed import seed_all
from app.domain.tenancy import Register, Store
from app.services.checkout import CartLineInput, create_pos_sale
from app.services.voids import VoidError, VoidNotPermittedError, void_order


@pytest.fixture
def store_setup(db):
    tenant = seed_all(db)
    store = db.query(Store).filter(Store.tenant_id == tenant.id).first()
    register = db.query(Register).filter(Register.store_id == store.id).first()

    cashier_role = db.query(Role).filter(Role.tenant_id == tenant.id, Role.name == "Cashier").first()
    cashier = User(tenant_id=tenant.id, name="Void Test Cashier", email="void-cashier@test-fixture.local", password_hash="x")
    db.add(cashier)
    db.flush()
    db.add(UserRole(user_id=cashier.id, role_id=cashier_role.id, store_id=store.id))

    inventory_role = db.query(Role).filter(Role.tenant_id == tenant.id, Role.name == "Inventory Manager").first()
    no_void_user = User(tenant_id=tenant.id, name="No Void Permission User", email="no-void@test-fixture.local", password_hash="x")
    db.add(no_void_user)
    db.flush()
    db.add(UserRole(user_id=no_void_user.id, role_id=inventory_role.id, store_id=store.id))

    tax = Tax(tenant_id=tenant.id, name="Standard", rate_basis_points=2100)
    db.add(tax)
    db.flush()
    category = db.query(Category).filter(Category.slug == "grocery", Category.tenant_id == tenant.id).first()
    product = Product(
        tenant_id=tenant.id, category_id=category.id, tax_id=tax.id, sku="VOID-1",
        name="Void Test Item", price_minor=1000, currency="EUR", unit="piece",
    )
    db.add(product)
    db.flush()
    db.add(CashierSession(tenant_id=tenant.id, store_id=store.id, register_id=register.id, cashier_user_id=cashier.id, opening_cash_minor=0))
    db.commit()

    order = create_pos_sale(
        db, tenant_id=tenant.id, store_id=store.id, register_id=register.id,
        cashier_user_id=cashier.id, lines=[CartLineInput(product.id, 3)],
        payment_method=PaymentMethod.CASH,
    )
    db.commit()

    return {
        "tenant_id": tenant.id, "store_id": store.id, "product_id": product.id,
        "cashier_principal": Principal(user_id=cashier.id, tenant_id=tenant.id, role="Cashier", store_id=store.id),
        "no_void_principal": Principal(user_id=no_void_user.id, tenant_id=tenant.id, role="Inventory Manager", store_id=store.id),
        "order_id": order.id,
        "original_receipt_number": order.receipt_number,
    }


def test_cashier_can_void_a_same_day_sale(db, store_setup):
    assert principal_has_permission(db, store_setup["cashier_principal"], "orders.void")
    order = void_order(
        db, tenant_id=store_setup["tenant_id"], order_id=store_setup["order_id"],
        principal=store_setup["cashier_principal"], reason="Rang up the wrong item",
    )
    assert order.status == OrderStatus.VOIDED
    assert order.voided_by_user_id == store_setup["cashier_principal"].user_id
    assert order.voided_reason == "Rang up the wrong item"
    assert order.voided_at is not None


def test_void_keeps_the_original_receipt_number(db, store_setup):
    order = void_order(
        db, tenant_id=store_setup["tenant_id"], order_id=store_setup["order_id"], principal=store_setup["cashier_principal"],
    )
    assert order.receipt_number == store_setup["original_receipt_number"]


def test_void_reverses_the_inventory_decrement(db, store_setup):
    before = db.query(InventoryLedger).filter(
        InventoryLedger.reference_type == "order", InventoryLedger.reference_id == str(store_setup["order_id"])
    ).all()
    assert sum(row.quantity_delta for row in before) == -3  # the original SALE

    void_order(db, tenant_id=store_setup["tenant_id"], order_id=store_setup["order_id"], principal=store_setup["cashier_principal"])
    db.flush()

    after = db.query(InventoryLedger).filter(
        InventoryLedger.reference_type == "order", InventoryLedger.reference_id == str(store_setup["order_id"])
    ).all()
    void_rows = [r for r in after if r.event_type == LedgerEventType.VOID]
    assert len(void_rows) == 1
    assert void_rows[0].quantity_delta == 3  # reversal of the -3 SALE
    assert sum(row.quantity_delta for row in after) == 0  # net stock impact is zero


def test_a_prior_day_sale_cannot_be_voided(db, store_setup):
    order = db.get(Order, store_setup["order_id"])
    order.created_at = dt.datetime.utcnow() - dt.timedelta(days=1)
    db.flush()
    with pytest.raises(VoidError, match="refunded, not voided"):
        void_order(db, tenant_id=store_setup["tenant_id"], order_id=store_setup["order_id"], principal=store_setup["cashier_principal"])


def test_an_already_voided_order_cannot_be_voided_again(db, store_setup):
    void_order(db, tenant_id=store_setup["tenant_id"], order_id=store_setup["order_id"], principal=store_setup["cashier_principal"])
    db.flush()
    with pytest.raises(VoidError):
        void_order(db, tenant_id=store_setup["tenant_id"], order_id=store_setup["order_id"], principal=store_setup["cashier_principal"])


def test_a_user_without_orders_void_permission_is_refused(db, store_setup):
    with pytest.raises(VoidNotPermittedError):
        void_order(db, tenant_id=store_setup["tenant_id"], order_id=store_setup["order_id"], principal=store_setup["no_void_principal"])
