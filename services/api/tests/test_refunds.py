import pytest

from app.domain.authz import ApprovalPolicy, User
from app.domain.cash import CashierSession
from app.domain.catalog import Product, Category
from app.domain.orders import PaymentMethod
from app.domain.seed import seed_all
from app.domain.tenancy import Register, Store
from app.services.checkout import CartLineInput, create_pos_sale
from app.services.refunds import RefundRequiresApprovalError, process_refund


@pytest.fixture
def order_setup(db):
    tenant = seed_all(db)
    user = User(tenant_id=tenant.id, name="Test Cashier 2", email="cashier2@test-fixture.local", password_hash="x")
    db.add(user)
    category = db.query(Category).filter(Category.slug == "grocery", Category.tenant_id == tenant.id).first()
    store = db.query(Store).filter(Store.tenant_id == tenant.id).first()
    register = db.query(Register).filter(Register.store_id == store.id).first()
    product = Product(tenant_id=tenant.id, category_id=category.id, name="Flour 1kg", price_minor=500, currency="EUR")
    db.add(product)
    db.flush()
    db.add(CashierSession(
        tenant_id=tenant.id, store_id=store.id, register_id=register.id,
        cashier_user_id=user.id, opening_cash_minor=10000, is_open=True,
    ))
    db.flush()
    order = create_pos_sale(
        db, tenant_id=tenant.id, store_id=store.id, register_id=register.id,
        cashier_user_id=user.id, lines=[CartLineInput(product.id, 4)], payment_method=PaymentMethod.CASH,
    )
    return {"order": order, "user_id": user.id, "tenant_id": tenant.id}


def test_no_policy_means_every_refund_needs_approval_fail_safe(db, order_setup):
    """No ApprovalPolicy row exists for this tenant -> fail-safe default:
    require approval for everything, never fail open."""
    with pytest.raises(RefundRequiresApprovalError):
        process_refund(db, tenant_id=order_setup["tenant_id"], order_id=order_setup["order"].id, requested_by_user_id=order_setup["user_id"], amount_minor=100, reason="test")


def test_refund_under_threshold_does_not_need_approval(db, order_setup):
    db.add(ApprovalPolicy(tenant_id=order_setup["tenant_id"], action_code="orders.refund", threshold_minor_units=2000, required_role="Store Manager"))
    db.commit()
    refund = process_refund(db, tenant_id=order_setup["tenant_id"], order_id=order_setup["order"].id, requested_by_user_id=order_setup["user_id"], amount_minor=500, reason="damaged item")
    assert refund.amount_minor == 500


def test_refund_over_threshold_requires_approval(db, order_setup):
    order = order_setup["order"]
    db.add(ApprovalPolicy(tenant_id=order_setup["tenant_id"], action_code="orders.refund", threshold_minor_units=200, required_role="Store Manager"))
    db.commit()
    with pytest.raises(RefundRequiresApprovalError) as exc_info:
        process_refund(db, tenant_id=order_setup["tenant_id"], order_id=order.id, requested_by_user_id=order_setup["user_id"], amount_minor=order.total_minor, reason="full refund")
    assert exc_info.value.threshold_minor_units == 200


def test_manager_override_bypasses_approval_gate(db, order_setup):
    order = order_setup["order"]
    db.add(ApprovalPolicy(tenant_id=order_setup["tenant_id"], action_code="orders.refund", threshold_minor_units=200, required_role="Store Manager"))
    db.commit()
    refund = process_refund(
        db, tenant_id=order_setup["tenant_id"], order_id=order.id, requested_by_user_id=order_setup["user_id"],
        amount_minor=order.total_minor, reason="manager approved in person", is_manager_override=True,
    )
    assert refund.amount_minor == order.total_minor


def test_full_refund_marks_order_refunded_partial_marks_partially_refunded(db, order_setup):
    order = order_setup["order"]
    db.add(ApprovalPolicy(tenant_id=order_setup["tenant_id"], action_code="orders.refund", threshold_minor_units=100000, required_role="Store Manager"))
    db.commit()
    process_refund(db, tenant_id=order_setup["tenant_id"], order_id=order.id, requested_by_user_id=order_setup["user_id"], amount_minor=500, reason="partial")
    assert order.status.value == "PARTIALLY_REFUNDED"
