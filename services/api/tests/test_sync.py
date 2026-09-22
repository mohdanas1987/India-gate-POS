"""
Phase 8 (rebuild) — tests for the previously-missing POST /api/v1/sync/events
endpoint. This is what the CTO audit (commit c4bfb82) flagged as entirely
absent: the desktop outbox engine had nowhere to deliver to.

NOTE on test style: every other route-adjacent test in this repo
(test_checkout.py, test_refunds.py) calls service functions directly with
the `db` fixture rather than going through FastAPI's TestClient. That's
not a style choice this file can safely deviate from: `require_permission`
(app/core/rbac.py) opens its OWN `SessionLocal()` rather than accepting the
request-scoped `db` session via Depends, so a TestClient-driven request
would resolve permissions against a separate database connection that
cannot see this test's uncommitted (savepoint-only) seed data — it would
fail with a false 403, not because of a real bug. So this file calls the
route FUNCTION directly, passing the test's own `db` session and a
manually-built Principal, exactly like the rest of this test suite does.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.api.v1.sync import SyncEventIn, ingest_sync_event
from app.api.v1.orders import get_order
from app.core.security import Principal
from app.domain.authz import User
from app.domain.cash import CashierSession
from app.domain.catalog import Category, Product, Tax
from app.domain.seed import seed_all
from app.domain.sync import InboxEvent
from app.domain.tenancy import Register, Store


@pytest.fixture
def synced_setup(db):
    tenant = seed_all(db)
    user = User(tenant_id=tenant.id, name="Offline Cashier", email="offline-cashier@test-fixture.local", password_hash="x")
    db.add(user)
    tax = Tax(tenant_id=tenant.id, name="Standard", rate_basis_points=2100)
    db.add(tax)
    db.flush()
    category = db.query(Category).filter(Category.slug == "grocery", Category.tenant_id == tenant.id).first()
    store = db.query(Store).filter(Store.tenant_id == tenant.id).first()
    register = db.query(Register).filter(Register.store_id == store.id).first()
    product = Product(
        tenant_id=tenant.id, category_id=category.id, tax_id=tax.id, sku="FLOUR-1KG",
        name="Atta Flour 1kg", price_minor=249, currency="EUR", unit="piece",
    )
    db.add(product)
    db.add(CashierSession(
        tenant_id=tenant.id, store_id=store.id, register_id=register.id,
        cashier_user_id=user.id, opening_cash_minor=5000, is_open=True,
    ))
    db.flush()
    db.commit()

    principal = Principal(user_id=user.id, tenant_id=tenant.id, role="Cashier", store_id=store.id)
    return {"principal": principal, "product_id": product.id, "register_id": register.id}


def _event(product_id: int, register_id: int, quantity: int = 2) -> SyncEventIn:
    return SyncEventIn(
        event_id=str(uuid.uuid4()),
        aggregate_type="order",
        aggregate_id="local-order-1",
        sequence=1,
        event_type="order.created",
        payload={
            "register_id": register_id,
            "payment_method": "CASH",
            "lines": [{"product_id": product_id, "quantity": quantity}],
        },
    )


def test_sync_event_creates_a_real_order(db, synced_setup):
    body = _event(synced_setup["product_id"], synced_setup["register_id"])

    result = ingest_sync_event(body, db, synced_setup["principal"])
    assert result.status == "processed"
    assert result.order_id is not None

    order = get_order(result.order_id, db, synced_setup["principal"])
    assert order.subtotal_minor == 249 * 2


def test_replaying_the_same_event_id_is_idempotent_not_duplicated(db, synced_setup):
    body = _event(synced_setup["product_id"], synced_setup["register_id"])

    first = ingest_sync_event(body, db, synced_setup["principal"])
    assert first.status == "processed"

    # Exact same event_id sent again (simulating a client retry after a
    # dropped response) must NOT create a second order.
    second = ingest_sync_event(body, db, synced_setup["principal"])
    assert second.status == "already_processed"
    assert second.order_id is None

    inbox_rows = db.query(InboxEvent).filter(InboxEvent.event_id == body.event_id).all()
    assert len(inbox_rows) == 1
    assert inbox_rows[0].processed_at is not None


def test_a_bad_product_id_is_recorded_as_a_failure_not_a_silent_partial_order(db, synced_setup):
    body = _event(product_id=999999, register_id=synced_setup["register_id"])

    with pytest.raises(HTTPException) as exc_info:
        ingest_sync_event(body, db, synced_setup["principal"])
    assert exc_info.value.status_code == 422

    inbox_rows = db.query(InboxEvent).filter(InboxEvent.event_id == body.event_id).all()
    assert len(inbox_rows) == 1
    assert inbox_rows[0].processed_at is None
    assert inbox_rows[0].retry_count == 1
    assert "999999" in inbox_rows[0].last_error

    # The critical assertion: the failed attempt must not have left a
    # broken half-written Order committed to the database.
    from app.domain.orders import Order
    orphaned_orders = db.query(Order).filter(Order.total_minor == 0, Order.status == "OPEN").all()
    assert orphaned_orders == []


def test_device_sequence_gap_is_recorded_as_a_conflict(db, synced_setup):
    """CTO audit of 0cfd8ca, finding #19: a device that skips a sequence
    number (event 1 delivered, event 2 lost, event 3 arrives next) must
    have that gap recorded, not silently ignored."""
    from app.domain.sync import Conflict, DeviceSyncState
    from app.domain.tenancy import Device

    first = _event(synced_setup["product_id"], synced_setup["register_id"])
    first.sequence = 1
    result1 = ingest_sync_event(first, db, synced_setup["principal"])
    assert result1.status == "processed"

    # Sequence 2 is skipped entirely — simulate event 3 arriving next.
    third = _event(synced_setup["product_id"], synced_setup["register_id"])
    third.sequence = 3
    result3 = ingest_sync_event(third, db, synced_setup["principal"])
    assert result3.status == "processed"

    device = db.query(Device).filter(Device.fingerprint == f"unregistered-device-user-{synced_setup['principal'].user_id}").first()
    state = db.get(DeviceSyncState, device.id)
    assert state.last_sequence_received == 3
    assert state.failed_count == 1

    gap = (
        db.query(Conflict)
        .filter(Conflict.aggregate_type == "device_sequence", Conflict.aggregate_id == str(device.id))
        .first()
    )
    assert gap is not None
    assert gap.local_value == "2"  # the expected-next sequence that never arrived
    assert gap.remote_value == "3"
    assert gap.resolution == "PENDING"


def test_unknown_event_type_is_rejected_not_silently_accepted(db, synced_setup):
    body = _event(synced_setup["product_id"], synced_setup["register_id"])
    body.event_type = "product.price_changed"  # not implemented server-side yet

    with pytest.raises(HTTPException) as exc_info:
        ingest_sync_event(body, db, synced_setup["principal"])
    assert exc_info.value.status_code == 422
    assert "Unsupported event_type" in exc_info.value.detail


# --- Phase 22.1: cash_session.open sync event (offline shift-start) ---


def _cash_session_open_event(register_id: int, opening_cash_minor: int = 10000, sequence: int = 1) -> SyncEventIn:
    return SyncEventIn(
        event_id=str(uuid.uuid4()),
        aggregate_type="cash_session",
        aggregate_id="local-session-1",
        sequence=sequence,
        event_type="cash_session.open",
        payload={"register_id": register_id, "opening_cash_minor": opening_cash_minor},
    )


@pytest.fixture
def unopened_register_setup(db):
    """Same shape as synced_setup, but with NO CashierSession pre-opened —
    needed because synced_setup's register already has one open, which
    would make every 'happy path' cash_session.open test hit the
    already-open conflict branch instead."""
    tenant = seed_all(db)
    user = User(tenant_id=tenant.id, name="Offline Cashier 2", email="offline-cashier-2@test-fixture.local", password_hash="x")
    db.add(user)
    db.flush()
    db.commit()

    store = db.query(Store).filter(Store.tenant_id == tenant.id).first()
    register = db.query(Register).filter(Register.store_id == store.id).first()

    principal = Principal(user_id=user.id, tenant_id=tenant.id, role="Cashier", store_id=store.id)
    return {"principal": principal, "register_id": register.id}


def test_cash_session_open_sync_event_creates_a_real_session(db, unopened_register_setup):
    body = _cash_session_open_event(unopened_register_setup["register_id"])

    result = ingest_sync_event(body, db, unopened_register_setup["principal"])
    assert result.status == "processed"

    session = (
        db.query(CashierSession)
        .filter(CashierSession.register_id == unopened_register_setup["register_id"], CashierSession.is_open.is_(True))
        .first()
    )
    assert session is not None
    assert session.opening_cash_minor == 10000
    assert session.cashier_user_id == unopened_register_setup["principal"].user_id


def test_cash_session_open_is_idempotent_on_replay(db, unopened_register_setup):
    body = _cash_session_open_event(unopened_register_setup["register_id"])

    first = ingest_sync_event(body, db, unopened_register_setup["principal"])
    assert first.status == "processed"

    second = ingest_sync_event(body, db, unopened_register_setup["principal"])
    assert second.status == "already_processed"

    # Only one CashierSession must exist — the replay must not have opened
    # a second one nor recorded a spurious conflict.
    sessions = (
        db.query(CashierSession)
        .filter(CashierSession.register_id == unopened_register_setup["register_id"], CashierSession.is_open.is_(True))
        .all()
    )
    assert len(sessions) == 1


def test_cash_session_open_without_permission_is_rejected(db, unopened_register_setup):
    """A principal whose role holds no cash.manage_session (e.g. Inventory
    Manager, per seed.py) must be rejected — not silently allowed to open a
    register session just because it reached this endpoint via the
    orders.create-gated sync route."""
    body = _cash_session_open_event(unopened_register_setup["register_id"])
    no_permission_principal = Principal(
        user_id=unopened_register_setup["principal"].user_id,
        tenant_id=unopened_register_setup["principal"].tenant_id,
        role="Inventory Manager",
        store_id=unopened_register_setup["principal"].store_id,
    )

    with pytest.raises(HTTPException) as exc_info:
        ingest_sync_event(body, db, no_permission_principal)
    assert exc_info.value.status_code == 403

    sessions = (
        db.query(CashierSession)
        .filter(CashierSession.register_id == unopened_register_setup["register_id"], CashierSession.is_open.is_(True))
        .all()
    )
    assert sessions == []


def test_cash_session_open_for_a_register_in_another_store_is_rejected(db, unopened_register_setup):
    """Register-ownership enforcement (same discipline as the direct
    cash.py route and get_current_session): a register that does not
    belong to the caller's own store must be refused, even though this
    request arrived through the sync endpoint rather than
    POST /api/v1/cash/session/open directly."""
    from app.domain.tenancy import Store as StoreModel

    other_store = StoreModel(tenant_id=unopened_register_setup["principal"].tenant_id, name="Other Store")
    db.add(other_store)
    db.flush()
    other_register = Register(store_id=other_store.id, name="Other Register")
    db.add(other_register)
    db.flush()
    db.commit()

    body = _cash_session_open_event(other_register.id)

    with pytest.raises(HTTPException) as exc_info:
        ingest_sync_event(body, db, unopened_register_setup["principal"])
    assert exc_info.value.status_code == 403

    sessions = db.query(CashierSession).filter(CashierSession.register_id == other_register.id).all()
    assert sessions == []


def test_cash_session_already_open_is_recorded_as_conflict_not_rejected(db, synced_setup):
    """synced_setup's register already has an open CashierSession (opened
    directly, simulating another device or an earlier online open). An
    offline device's cash_session.open syncing in for that same register
    must NOT hard-fail — that would strand every order.created event the
    same offline batch is about to sync right behind it. It must instead
    record a Conflict and report success, reusing the existing session."""
    from app.domain.sync import Conflict

    existing_session = (
        db.query(CashierSession)
        .filter(CashierSession.register_id == synced_setup["register_id"], CashierSession.is_open.is_(True))
        .first()
    )
    assert existing_session is not None  # sanity: synced_setup really does pre-open one

    body = _cash_session_open_event(synced_setup["register_id"])
    result = ingest_sync_event(body, db, synced_setup["principal"])

    assert result.status == "processed"

    # Still exactly one open session on that register — the conflicting
    # offline open did not create a duplicate.
    open_sessions = (
        db.query(CashierSession)
        .filter(CashierSession.register_id == synced_setup["register_id"], CashierSession.is_open.is_(True))
        .all()
    )
    assert len(open_sessions) == 1
    assert open_sessions[0].id == existing_session.id

    conflict = (
        db.query(Conflict)
        .filter(Conflict.aggregate_type == "cash_session", Conflict.aggregate_id == str(synced_setup["register_id"]))
        .first()
    )
    assert conflict is not None
    assert conflict.resolution == "PENDING"
    assert str(existing_session.id) in conflict.remote_value

    # The event must be marked processed (not left retrying forever) even
    # though it hit a conflict.
    inbox_row = db.query(InboxEvent).filter(InboxEvent.event_id == body.event_id).first()
    assert inbox_row.processed_at is not None


def test_cash_session_open_then_order_created_in_same_batch_succeeds(db, unopened_register_setup):
    """The ordering guarantee this whole feature depends on: an offline
    device enqueues cash_session.open at a lower sequence number than the
    order.created events from the same shift (apps/pos/desktop/electron/
    offlineShift.ts + outboxSync.ts's ORDER BY sequence ASC). This proves
    that once both arrive at the server in that order, the second event
    finds a real session to attach the sale to — the actual point of
    Phase 22.1, not just that each event type works in isolation."""
    tenant_principal = unopened_register_setup["principal"]
    register_id = unopened_register_setup["register_id"]

    open_body = _cash_session_open_event(register_id, sequence=1)
    open_result = ingest_sync_event(open_body, db, tenant_principal)
    assert open_result.status == "processed"

    # Need a real product to sell — reuse seed_all's category/tax setup.
    tenant = db.query(User).filter(User.id == tenant_principal.user_id).first().tenant_id
    category = db.query(Category).filter(Category.slug == "grocery", Category.tenant_id == tenant).first()
    tax = Tax(tenant_id=tenant, name="Standard 22.1", rate_basis_points=2100)
    db.add(tax)
    db.flush()
    product = Product(
        tenant_id=tenant, category_id=category.id, tax_id=tax.id, sku="OFFLINE-SHIFT-TEST",
        name="Offline Shift Test Item", price_minor=500, currency="EUR", unit="piece",
    )
    db.add(product)
    db.flush()
    db.commit()

    order_body = _event(product.id, register_id, quantity=1)
    order_body.sequence = 2
    order_result = ingest_sync_event(order_body, db, tenant_principal)
    assert order_result.status == "processed"
    assert order_result.order_id is not None
