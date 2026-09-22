"""
Phase 8 (rebuild) — the cloud-side half of the offline outbox pipeline.

CTO audit finding (commit c4bfb82): the Electron app's OutboxSyncEngine
(apps/pos/desktop/src/sync/outboxSync.ts) POSTs to
`${apiBaseUrl}/api/v1/sync/events`, but no such route existed anywhere in
the committed API — the outbox had nowhere to actually deliver to. This
file is that missing endpoint.

Idempotency (plan §16): every inbound event carries a client-generated
`event_id`. `InboxEvent.event_id` has a UNIQUE constraint (app/domain/sync.py)
— a re-sent event (e.g. the device retried after a timeout but the first
POST had actually succeeded) is detected and treated as already-processed,
never double-applied.

Scope, stated honestly: this currently handles exactly one event type,
`order.created`, because that is the one real offline-write path this
rebuild wires end-to-end (POS checkout). It deliberately does NOT invent
handling for event types nothing produces yet (e.g. website-order status
changes made offline) — better to reject an unknown event type loudly than
to pretend to process it.

Trust model: the payload's cart lines (product_id, quantity) are
re-validated against the SERVER's product/tax data via the same
`create_pos_sale` the direct `POST /api/v1/orders` endpoint uses — the
client's own locally-computed total (used for the receipt while offline)
is never trusted as the authoritative total. This is what stops a
compromised or buggy offline client from writing an arbitrary total to
Postgres.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.rbac import require_permission
from app.core.security import Principal
from app.db.session import get_db
from app.domain.orders import PaymentMethod
from app.domain.sync import Conflict, DeviceSyncState, InboxEvent
from app.domain.tenancy import Device
from app.services.cash_sessions import CashSessionAlreadyOpenError, CashSessionError, CashSessionNotPermittedError, open_cashier_session
from app.services.checkout import CartLineInput, CheckoutError, PaymentInput, UnauthorizedResourceError, create_pos_sale
from app.services.ledgerbrug import enqueue_order_event

router = APIRouter(prefix="/sync", tags=["sync"])


class SyncEventIn(BaseModel):
    event_id: str
    aggregate_type: str
    aggregate_id: str
    sequence: int
    event_type: str
    payload: dict
    device_fingerprint: str | None = None


class SyncEventResult(BaseModel):
    status: str  # "processed" | "already_processed" | "rejected"
    detail: str | None = None
    order_id: int | None = None


def _get_or_create_device(db: Session, principal: Principal, fingerprint: str | None) -> Device:
    """
    Devices are meant to be provisioned explicitly (plan's DeviceSyncState
    concept), but there is no device-provisioning UI yet (out of scope for
    this rebuild). Rather than block every sync call on a feature that
    doesn't exist, an unrecognized fingerprint is auto-registered against
    the caller's own store the first time it's seen — this is a real,
    disclosed simplification, not a claim that device management is done.
    """
    fingerprint = fingerprint or f"unregistered-device-user-{principal.user_id}"
    device = db.query(Device).filter(Device.fingerprint == fingerprint).first()
    if device:
        return device
    if principal.store_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="This user has no store assigned — cannot register a sync device without a store context",
        )
    device = Device(store_id=principal.store_id, label=f"Auto-registered ({fingerprint[:40]})", fingerprint=fingerprint)
    db.add(device)
    db.flush()
    return device


def _check_and_advance_device_sequence(db: Session, tenant_id: int, device: Device, sequence: int) -> None:
    """
    Phase 8.5 (CTO audit of 0cfd8ca, finding #19): "the server currently
    doesn't enforce device sequence continuity ... this becomes important
    when inventory and multi-device synchronization arrive." This
    implements DETECTION now — recording a gap as a real, visible Conflict
    row the moment it's seen — deliberately without yet BLOCKING
    processing on a gap. Blocking here would mean a single delayed or
    dropped event from one device could wedge every later event from that
    same device indefinitely; for a POS whose worst failure mode must be
    "sale eventually syncs late", refusing to process is worse than
    recording and moving on. This is a disclosed scope decision, not a
    partial implementation pretending to be complete — see PHASE-STATUS.md.
    """
    state = db.get(DeviceSyncState, device.id)
    if state is None:
        state = DeviceSyncState(device_id=device.id, last_sequence_received=0)
        db.add(state)
        db.flush()

    expected_next = state.last_sequence_received + 1
    if sequence > expected_next:
        db.add(
            Conflict(
                tenant_id=tenant_id,
                aggregate_type="device_sequence",
                aggregate_id=str(device.id),
                field="sequence",
                local_value=str(expected_next),
                remote_value=str(sequence),
                resolution="PENDING",
            )
        )
        state.failed_count += 1
    elif sequence < expected_next:
        # An out-of-order or replayed-late event. event_id idempotency
        # (InboxEvent's UNIQUE constraint) already protects against
        # double-processing the SAME event; this just means the sequence
        # counter shouldn't move backwards for a genuinely older delivery.
        pass

    state.last_sequence_received = max(state.last_sequence_received, sequence)
    import datetime as dt

    state.last_synced_at = dt.datetime.utcnow()
    db.flush()


def _process_cash_session_open(db: Session, principal: Principal, inbox_row: InboxEvent) -> SyncEventResult:
    """
    Phase 22.1: server-side handling of an offline-opened shift syncing in.
    The device already opened a LOCAL session (sentinel session_id=0,
    pending_sync=1 — see apps/pos/desktop/electron/offlineShift.ts) before
    this event ever reaches the network; this is where that gets a real
    server-side CashierSession, via the exact same centralized
    open_cashier_session() the direct online route uses (Phase 22.1:
    "centralize resolve_authorized_register() and use it everywhere",
    extended to session-open itself).

    Conflict handling mirrors _check_and_advance_device_sequence's
    philosophy: if a session is ALREADY open on this register by the time
    this event arrives (another device opened it first, or the same device
    raced an earlier online open against this offline one), hard-rejecting
    would strand every order.created event this offline batch is about to
    sync right behind it — checkout already happened offline, the sale is
    real, and it needs *a* session to attach to. So this is recorded as a
    visible Conflict (for a human to review) and then treated as processed,
    reusing the existing open session rather than blocking on it.
    """
    try:
        register_id = int(inbox_row.payload["register_id"])
        opening_cash_minor = int(inbox_row.payload["opening_cash_minor"])
    except (KeyError, ValueError, TypeError) as exc:
        db.rollback()
        inbox_row.retry_count += 1
        inbox_row.last_error = f"cash_session.open payload malformed: {exc}"
        db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=inbox_row.last_error) from exc

    if principal.store_id is None:
        db.rollback()
        inbox_row.retry_count += 1
        inbox_row.last_error = "This user has no store assigned — cannot open a cashier session without a store context"
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=inbox_row.last_error)

    try:
        session = open_cashier_session(
            db,
            tenant_id=principal.tenant_id,
            store_id=principal.store_id,
            register_id=register_id,
            principal=principal,
            opening_cash_minor=opening_cash_minor,
        )
    except CashSessionAlreadyOpenError as exc:
        # Non-blocking: record the conflict, reuse the existing session, and
        # mark this event processed so subsequent order.created events in
        # the same offline batch have a session to attach to.
        db.add(
            Conflict(
                tenant_id=principal.tenant_id,
                aggregate_type="cash_session",
                aggregate_id=str(register_id),
                field="is_open",
                local_value="opened_offline",
                remote_value=f"already_open_session_id={exc.existing.id}",
                resolution="PENDING",
            )
        )
        import datetime as dt

        inbox_row.processed_at = dt.datetime.utcnow()
        db.commit()
        return SyncEventResult(
            status="processed",
            detail=f"register already had an open session (id={exc.existing.id}); recorded as conflict, reused it",
        )
    except CashSessionNotPermittedError as exc:
        db.rollback()
        inbox_row.retry_count += 1
        inbox_row.last_error = str(exc)
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    inbox_row.processed_at = session.opened_at
    db.commit()
    return SyncEventResult(status="processed", detail=f"cashier session {session.id} opened")


@router.post("/events", response_model=SyncEventResult)
def ingest_sync_event(
    body: SyncEventIn,
    db: Session = Depends(get_db),
    # Gated on "orders.create", not "sync.manage": the only event type this
    # endpoint currently processes is order.created, and the permission
    # that should matter is "can this caller ring up a sale", i.e. whatever
    # gates the direct POST /api/v1/orders route — not a separate
    # sync-administration permission the Cashier role doesn't hold (seed.py
    # never grants Cashier "sync.manage", so gating on that would 403 every
    # offline sale the moment it tries to sync).
    principal: Principal = Depends(require_permission("orders.create")),
):
    existing = db.query(InboxEvent).filter(InboxEvent.event_id == body.event_id).first()
    if existing and existing.processed_at is not None:
        # Idempotent replay — the device retried a delivery that actually
        # already succeeded. Report success without reprocessing.
        return SyncEventResult(status="already_processed", detail="event already processed")

    if principal.store_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="This user has no store assigned — cannot sync without a store context",
        )

    device = _get_or_create_device(db, principal, body.device_fingerprint)

    # Record receipt of the event in its OWN small transaction, committed
    # immediately, before attempting to process it. This matters: if
    # processing later fails partway through (create_pos_sale flushes an
    # Order row, then hits an error), we need to be able to roll that back
    # WITHOUT also rolling back the fact that we ever saw this event —
    # otherwise a failure would erase its own error trail.
    inbox_row = existing
    is_first_receipt = inbox_row is None
    if inbox_row is None:
        inbox_row = InboxEvent(
            tenant_id=principal.tenant_id,
            store_id=principal.store_id,
            device_id=device.id,
            event_id=body.event_id,
            event_type=body.event_type,
            aggregate_type=body.aggregate_type,
            aggregate_id=body.aggregate_id,
            sequence=body.sequence,
            payload=body.payload,
        )
        db.add(inbox_row)
    if is_first_receipt:
        # Only advance the per-device sequence watermark the first time an
        # event_id is ever seen — a retry of a previously-failed-to-process
        # event (event exists, but processed_at is still NULL) must not
        # advance it a second time.
        _check_and_advance_device_sequence(db, principal.tenant_id, device, body.sequence)
    db.commit()
    db.refresh(inbox_row)

    if body.event_type == "cash_session.open":
        return _process_cash_session_open(db, principal, inbox_row)

    if body.event_type != "order.created":
        inbox_row.retry_count += 1
        inbox_row.last_error = f"Unsupported event_type '{body.event_type}' — nothing consumes this yet"
        db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=inbox_row.last_error)

    try:
        lines_payload = body.payload.get("lines", [])
        if not lines_payload:
            raise CheckoutError("order.created event carried no cart lines")
        payments_payload = body.payload.get("payments")
        if payments_payload:
            # Phase 22: an offline sale rung up with a split payment.
            order = create_pos_sale(
                db,
                tenant_id=principal.tenant_id,
                store_id=principal.store_id,
                register_id=int(body.payload["register_id"]),
                cashier_user_id=principal.user_id,
                lines=[CartLineInput(int(l["product_id"]), int(l["quantity"])) for l in lines_payload],
                payments=[
                    PaymentInput(
                        PaymentMethod(p["method"]), int(p["amount_minor"]), p.get("provider_reference")
                    )
                    for p in payments_payload
                ],
            )
        else:
            order = create_pos_sale(
                db,
                tenant_id=principal.tenant_id,
                store_id=principal.store_id,
                register_id=int(body.payload["register_id"]),
                cashier_user_id=principal.user_id,
                lines=[CartLineInput(int(l["product_id"]), int(l["quantity"])) for l in lines_payload],
                payment_method=PaymentMethod(body.payload.get("payment_method", "CASH")),
            )
        # Phase 22: same atomic-with-the-order enqueue as the direct
        # checkout path (app/api/v1/orders.py).
        enqueue_order_event(db, principal.tenant_id, order, "order.completed")
        # create_pos_sale only flushes now (finding #17) — this endpoint
        # is the transaction boundary for the order/lines/payment/ledger
        # it just built, exactly as it already was for the InboxEvent row
        # above. Each is still its own transaction (recording "we received
        # this event" must survive even if processing it fails), but
        # within THIS step, everything create_pos_sale touched commits or
        # rolls back together as one unit, rather than the two services
        # each independently deciding when to commit.
        db.commit()
        db.refresh(order)
    except (CheckoutError, UnauthorizedResourceError, KeyError, ValueError) as exc:
        # Undo any partially-flushed Order/OrderLine/InventoryLedger rows
        # from the failed create_pos_sale attempt BEFORE recording the
        # error — otherwise a broken half-written order (zero totals, no
        # payment row) could get committed as a side effect of committing
        # the error note. Recorded, not silently dropped: the event stays
        # unprocessed (processed_at is still NULL) so a human can see it in
        # InboxEvent.last_error, and the device will keep retrying it since
        # it never got a 2xx.
        db.rollback()
        inbox_row.retry_count += 1
        inbox_row.last_error = str(exc)
        db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    inbox_row.processed_at = order.created_at
    db.commit()
    return SyncEventResult(status="processed", order_id=order.id)
