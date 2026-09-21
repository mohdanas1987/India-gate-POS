"""
Phase 22 (LedgerBrug dev-team question #6 — outbound webhook + offline
queue). Tests the queue-and-send mechanics with a fake transport, since
there is no real LedgerBrug endpoint to call yet (their contract doc was
never received — see app/services/ledgerbrug.py's module docstring).
"""
import datetime as dt

import pytest

from app.domain.authz import User
from app.domain.cash import CashierSession
from app.domain.catalog import Product, Tax, Category
from app.domain.integrations import LedgerBrugEventStatus, LedgerBrugOutboxEvent
from app.domain.orders import PaymentMethod
from app.domain.seed import seed_all
from app.domain.tenancy import Register, Store
from app.services.checkout import CartLineInput, create_pos_sale
from app.services.ledgerbrug import compute_backoff_seconds, enqueue_order_event, send_pending_events, sign_payload


@pytest.fixture
def order_setup(db):
    tenant = seed_all(db)
    user = User(tenant_id=tenant.id, name="Outbox Test Cashier", email="outbox-cashier@test-fixture.local", password_hash="x")
    db.add(user)
    tax = Tax(tenant_id=tenant.id, name="Standard", rate_basis_points=2100)
    db.add(tax)
    db.flush()
    category = db.query(Category).filter(Category.slug == "grocery", Category.tenant_id == tenant.id).first()
    store = db.query(Store).filter(Store.tenant_id == tenant.id).first()
    register = db.query(Register).filter(Register.store_id == store.id).first()
    product = Product(tenant_id=tenant.id, category_id=category.id, tax_id=tax.id, sku="OUTBOX-1", name="Outbox Test Item", price_minor=500, currency="EUR", unit="piece")
    db.add(product)
    db.flush()
    db.add(CashierSession(tenant_id=tenant.id, store_id=store.id, register_id=register.id, cashier_user_id=user.id, opening_cash_minor=0))
    db.commit()
    order = create_pos_sale(
        db, tenant_id=tenant.id, store_id=store.id, register_id=register.id,
        cashier_user_id=user.id, lines=[CartLineInput(product.id, 1)], payment_method=PaymentMethod.CASH,
    )
    db.commit()
    return {"tenant_id": tenant.id, "order": order}


def test_enqueue_writes_a_pending_event_with_the_expected_shape(db, order_setup):
    event = enqueue_order_event(db, order_setup["tenant_id"], order_setup["order"], "order.completed")
    db.commit()
    assert event.status == LedgerBrugEventStatus.PENDING
    assert event.payload["receipt_number"] == order_setup["order"].receipt_number
    assert event.payload["total_minor"] == order_setup["order"].total_minor
    assert event.payload["event_type"] == "order.completed"
    assert len(event.payload["lines"]) == 1


def test_send_pending_events_marks_success_on_2xx(db, order_setup):
    enqueue_order_event(db, order_setup["tenant_id"], order_setup["order"], "order.completed")
    db.commit()

    calls = []

    def fake_post(url, payload, headers):
        calls.append((url, payload, headers))
        return 200

    summary = send_pending_events(db, fake_post, "https://ledgerbrug.example/webhook", "shared-secret")
    db.commit()
    assert summary == {"sent": 1, "failed": 0, "still_pending": 0}
    assert len(calls) == 1
    event = db.query(LedgerBrugOutboxEvent).first()
    assert event.status == LedgerBrugEventStatus.SENT
    assert event.sent_at is not None
    # signature actually verifies against the secret used
    assert calls[0][2]["X-LedgerBrug-Signature"] == sign_payload(event.payload, "shared-secret")


def test_send_pending_events_retries_with_backoff_on_failure(db, order_setup):
    enqueue_order_event(db, order_setup["tenant_id"], order_setup["order"], "order.completed")
    db.commit()

    def failing_post(url, payload, headers):
        return 500

    send_pending_events(db, failing_post, "https://ledgerbrug.example/webhook", "shared-secret")
    db.commit()
    event = db.query(LedgerBrugOutboxEvent).first()
    assert event.status == LedgerBrugEventStatus.PENDING  # not FAILED yet, still retrying
    assert event.attempt_count == 1
    now = dt.datetime.now(event.next_attempt_at.tzinfo) if event.next_attempt_at.tzinfo else dt.datetime.utcnow()
    assert event.next_attempt_at > now  # backed off into the future
    assert event.last_error == "HTTP 500"

    # It should NOT be picked up again immediately, since it's not due yet.
    calls = []
    send_pending_events(db, lambda *a: calls.append(a) or 200, "https://ledgerbrug.example/webhook", "shared-secret")
    assert calls == []


def test_send_pending_events_gives_up_after_max_attempts(db, order_setup):
    event = enqueue_order_event(db, order_setup["tenant_id"], order_setup["order"], "order.completed")
    event.attempt_count = 9  # one below max_attempts=10
    event.next_attempt_at = dt.datetime.utcnow() - dt.timedelta(seconds=1)
    db.commit()

    summary = send_pending_events(db, lambda *a: 500, "https://ledgerbrug.example/webhook", "shared-secret", max_attempts=10)
    db.commit()
    assert summary["failed"] == 1
    event = db.query(LedgerBrugOutboxEvent).first()
    assert event.status == LedgerBrugEventStatus.FAILED


def test_compute_backoff_seconds_grows_and_is_capped():
    assert compute_backoff_seconds(0, cap_seconds=300) <= 300 * 1.2
    small = compute_backoff_seconds(1)
    large = compute_backoff_seconds(6)
    assert large > small
    assert compute_backoff_seconds(20, cap_seconds=300) <= 300 * 1.2
