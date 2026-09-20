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
from app.domain.sync import InboxEvent
from app.domain.tenancy import Device
from app.services.checkout import CartLineInput, CheckoutError, create_pos_sale

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
    db.commit()
    db.refresh(inbox_row)

    if body.event_type != "order.created":
        inbox_row.retry_count += 1
        inbox_row.last_error = f"Unsupported event_type '{body.event_type}' — nothing consumes this yet"
        db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=inbox_row.last_error)

    try:
        lines_payload = body.payload.get("lines", [])
        if not lines_payload:
            raise CheckoutError("order.created event carried no cart lines")
        order = create_pos_sale(
            db,
            tenant_id=principal.tenant_id,
            store_id=principal.store_id,
            register_id=int(body.payload["register_id"]),
            cashier_user_id=principal.user_id,
            lines=[CartLineInput(int(l["product_id"]), int(l["quantity"])) for l in lines_payload],
            payment_method=PaymentMethod(body.payload.get("payment_method", "CASH")),
        )
    except (CheckoutError, KeyError, ValueError) as exc:
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
