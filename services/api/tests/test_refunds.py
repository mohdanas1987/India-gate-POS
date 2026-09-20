import pytest

from app.core.security import Principal
from app.domain.authz import Approval, ApprovalPolicy, User
from app.domain.cash import CashierSession
from app.domain.catalog import Product, Category
from app.domain.orders import PaymentMethod, Refund
from app.domain.seed import seed_all
from app.domain.tenancy import Register, Store
from app.services.checkout import CartLineInput, create_pos_sale
from app.services.refunds import RefundError, RefundRequiresApprovalError, process_refund, resolve_approval


@pytest.fixture
def order_setup(db):
    tenant = seed_all(db)
    cashier = User(tenant_id=tenant.id, name="Test Cashier 2", email="cashier2@test-fixture.local", password_hash="x")
    manager = User(tenant_id=tenant.id, name="Test Manager", email="manager@test-fixture.local", password_hash="x")
    db.add(cashier)
    db.add(manager)
    category = db.query(Category).filter(Category.slug == "grocery", Category.tenant_id == tenant.id).first()
    store = db.query(Store).filter(Store.tenant_id == tenant.id).first()
    register = db.query(Register).filter(Register.store_id == store.id).first()
    product = Product(tenant_id=tenant.id, category_id=category.id, name="Flour 1kg", price_minor=500, currency="EUR")
    db.add(product)
    db.flush()
    db.add(CashierSession(
        tenant_id=tenant.id, store_id=store.id, register_id=register.id,
        cashier_user_id=cashier.id, opening_cash_minor=10000, is_open=True,
    ))
    db.flush()
    order = create_pos_sale(
        db, tenant_id=tenant.id, store_id=store.id, register_id=register.id,
        cashier_user_id=cashier.id, lines=[CartLineInput(product.id, 4)], payment_method=PaymentMethod.CASH,
    )
    cashier_principal = Principal(user_id=cashier.id, tenant_id=tenant.id, role="Cashier", store_id=store.id)
    manager_principal = Principal(user_id=manager.id, tenant_id=tenant.id, role="Store Manager", store_id=store.id)
    return {
        "order": order, "tenant_id": tenant.id,
        "cashier_principal": cashier_principal, "manager_principal": manager_principal,
    }


def test_no_policy_means_every_refund_needs_approval_fail_safe(db, order_setup):
    """No ApprovalPolicy row exists for this tenant -> fail-safe default:
    require approval for everything, never fail open."""
    with pytest.raises(RefundRequiresApprovalError):
        process_refund(
            db, tenant_id=order_setup["tenant_id"], order_id=order_setup["order"].id,
            principal=order_setup["cashier_principal"], amount_minor=100, reason="test",
        )


def test_refund_under_threshold_does_not_need_approval(db, order_setup):
    db.add(ApprovalPolicy(tenant_id=order_setup["tenant_id"], action_code="orders.refund", threshold_minor_units=2000, required_role="Store Manager"))
    db.commit()
    refund = process_refund(
        db, tenant_id=order_setup["tenant_id"], order_id=order_setup["order"].id,
        principal=order_setup["cashier_principal"], amount_minor=500, reason="damaged item",
    )
    assert refund.amount_minor == 500


def test_refund_over_threshold_creates_a_real_pending_approval(db, order_setup):
    """CTO audit finding #13: a refund needing approval must create an
    actual Approval row a manager can act on, not just return an error."""
    order = order_setup["order"]
    db.add(ApprovalPolicy(tenant_id=order_setup["tenant_id"], action_code="orders.refund", threshold_minor_units=200, required_role="Store Manager"))
    db.commit()
    with pytest.raises(RefundRequiresApprovalError) as exc_info:
        process_refund(
            db, tenant_id=order_setup["tenant_id"], order_id=order.id,
            principal=order_setup["cashier_principal"], amount_minor=order.total_minor, reason="full refund",
        )
    assert exc_info.value.threshold_minor_units == 200
    assert exc_info.value.approval_id is not None

    approval = db.get(Approval, exc_info.value.approval_id)
    assert approval.status == "REQUESTED"
    assert approval.context["order_id"] == order.id
    assert approval.context["amount_minor"] == order.total_minor
    # No refund should exist yet — only a pending request.
    assert db.query(Refund).filter(Refund.order_id == order.id).count() == 0


def test_manager_resolving_approval_actually_executes_the_refund(db, order_setup):
    """The second half of the real workflow: approving the Approval row
    is what creates the Refund — not the original request."""
    order = order_setup["order"]
    db.add(ApprovalPolicy(tenant_id=order_setup["tenant_id"], action_code="orders.refund", threshold_minor_units=200, required_role="Store Manager"))
    db.commit()
    with pytest.raises(RefundRequiresApprovalError) as exc_info:
        process_refund(
            db, tenant_id=order_setup["tenant_id"], order_id=order.id,
            principal=order_setup["cashier_principal"], amount_minor=order.total_minor, reason="full refund",
        )
    approval_id = exc_info.value.approval_id
    db.commit()

    approval = resolve_approval(
        db, tenant_id=order_setup["tenant_id"], approval_id=approval_id,
        resolver=order_setup["manager_principal"], approve=True,
    )
    assert approval.status == "APPROVED"
    refund = db.query(Refund).filter(Refund.order_id == order.id).first()
    assert refund is not None
    assert refund.amount_minor == order.total_minor
    assert refund.approval_id == approval.id


def test_manager_rejecting_approval_creates_no_refund(db, order_setup):
    order = order_setup["order"]
    db.add(ApprovalPolicy(tenant_id=order_setup["tenant_id"], action_code="orders.refund", threshold_minor_units=200, required_role="Store Manager"))
    db.commit()
    with pytest.raises(RefundRequiresApprovalError) as exc_info:
        process_refund(
            db, tenant_id=order_setup["tenant_id"], order_id=order.id,
            principal=order_setup["cashier_principal"], amount_minor=order.total_minor, reason="full refund",
        )
    approval_id = exc_info.value.approval_id
    db.commit()

    approval = resolve_approval(
        db, tenant_id=order_setup["tenant_id"], approval_id=approval_id,
        resolver=order_setup["manager_principal"], approve=False,
    )
    assert approval.status == "REJECTED"
    assert db.query(Refund).filter(Refund.order_id == order.id).count() == 0


def test_cashier_cannot_resolve_their_own_approval(db, order_setup):
    """CTO audit finding #14: the domain layer must independently verify
    the resolver actually holds orders.refund.override — a Cashier cannot
    self-approve by calling resolve_approval directly."""
    order = order_setup["order"]
    db.add(ApprovalPolicy(tenant_id=order_setup["tenant_id"], action_code="orders.refund", threshold_minor_units=200, required_role="Store Manager"))
    db.commit()
    with pytest.raises(RefundRequiresApprovalError) as exc_info:
        process_refund(
            db, tenant_id=order_setup["tenant_id"], order_id=order.id,
            principal=order_setup["cashier_principal"], amount_minor=order.total_minor, reason="full refund",
        )
    approval_id = exc_info.value.approval_id
    db.commit()

    with pytest.raises(RefundError):
        resolve_approval(
            db, tenant_id=order_setup["tenant_id"], approval_id=approval_id,
            resolver=order_setup["cashier_principal"], approve=True,
        )


def test_manager_override_bypasses_approval_gate(db, order_setup):
    order = order_setup["order"]
    db.add(ApprovalPolicy(tenant_id=order_setup["tenant_id"], action_code="orders.refund", threshold_minor_units=200, required_role="Store Manager"))
    db.commit()
    refund = process_refund(
        db, tenant_id=order_setup["tenant_id"], order_id=order.id, principal=order_setup["manager_principal"],
        amount_minor=order.total_minor, reason="manager approved in person", manager_override_requested=True,
    )
    assert refund.amount_minor == order.total_minor


def test_cashier_cannot_self_grant_override_even_if_requested(db, order_setup):
    """CTO audit finding #14: manager_override_requested=True from a
    Cashier principal must NOT bypass approval — the service checks the
    actual permission, it does not trust the boolean."""
    order = order_setup["order"]
    db.add(ApprovalPolicy(tenant_id=order_setup["tenant_id"], action_code="orders.refund", threshold_minor_units=200, required_role="Store Manager"))
    db.commit()
    with pytest.raises(RefundRequiresApprovalError):
        process_refund(
            db, tenant_id=order_setup["tenant_id"], order_id=order.id, principal=order_setup["cashier_principal"],
            amount_minor=order.total_minor, reason="cashier tries to self-override", manager_override_requested=True,
        )


def test_full_refund_marks_order_refunded_partial_marks_partially_refunded(db, order_setup):
    order = order_setup["order"]
    db.add(ApprovalPolicy(tenant_id=order_setup["tenant_id"], action_code="orders.refund", threshold_minor_units=100000, required_role="Store Manager"))
    db.commit()
    process_refund(
        db, tenant_id=order_setup["tenant_id"], order_id=order.id,
        principal=order_setup["cashier_principal"], amount_minor=500, reason="partial",
    )
    assert order.status.value == "PARTIALLY_REFUNDED"


def test_cumulative_refunds_cannot_exceed_order_total(db, order_setup):
    """CTO audit finding #12 (RELEASE BLOCKER): two individually-valid
    refunds must not be allowed to sum to more than the order total.
    Order total here is 4 * 500 = 2000."""
    order = order_setup["order"]
    db.add(ApprovalPolicy(tenant_id=order_setup["tenant_id"], action_code="orders.refund", threshold_minor_units=100000, required_role="Store Manager"))
    db.commit()

    first = process_refund(
        db, tenant_id=order_setup["tenant_id"], order_id=order.id,
        principal=order_setup["cashier_principal"], amount_minor=1600, reason="refund 1",
    )
    assert first.amount_minor == 1600

    # A second refund of 1600 against the same 2000 order must be rejected
    # — 1600 + 1600 = 3200, far more than the order ever contained.
    with pytest.raises(RefundError):
        process_refund(
            db, tenant_id=order_setup["tenant_id"], order_id=order.id,
            principal=order_setup["cashier_principal"], amount_minor=1600, reason="refund 2 (should fail)",
        )

    total_refunded = sum(r.amount_minor for r in db.query(Refund).filter(Refund.order_id == order.id).all())
    assert total_refunded == 1600


def test_second_refund_can_still_use_up_the_remaining_balance(db, order_setup):
    order = order_setup["order"]
    db.add(ApprovalPolicy(tenant_id=order_setup["tenant_id"], action_code="orders.refund", threshold_minor_units=100000, required_role="Store Manager"))
    db.commit()

    process_refund(
        db, tenant_id=order_setup["tenant_id"], order_id=order.id,
        principal=order_setup["cashier_principal"], amount_minor=1600, reason="refund 1",
    )
    second = process_refund(
        db, tenant_id=order_setup["tenant_id"], order_id=order.id,
        principal=order_setup["cashier_principal"], amount_minor=400, reason="refund 2 (exactly the remainder)",
    )
    assert second.amount_minor == 400
    assert order.status.value == "REFUNDED"
