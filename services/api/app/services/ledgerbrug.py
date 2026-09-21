"""
Phase 22 — outbound integration to LedgerBrug (dev-team question #6).

Two halves, deliberately separated:

  1. `enqueue_order_event()` — called from the same transaction as the
     order write (checkout, void, refund) so the outbox row and the order
     it describes are committed atomically: it is not possible for the
     order to exist without a queued event, or vice versa.

  2. `send_pending_events()` — the delivery side. NOT wired to a
     scheduler/worker in this codebase; there isn't one yet. This is a
     plain function meant to be invoked periodically (a cron entry, a
     simple asyncio loop in a management command, or whatever job runner
     gets adopted) — building a full background-job framework is out of
     scope for this integration and would be a bigger decision than
     LedgerBrug's webhook deserves to force.

Backoff mirrors apps/pos/desktop/electron/sync/outboxSync.ts's
computeBackoffMs() on purpose — same shape (2^n capped, with jitter) that
was already proven for exactly this "queue locally, retry a flaky HTTP
call" problem, just re-expressed in Python for the server side.

**Disclosed, load-bearing caveat**: the payload shape in
`build_event_payload()` is our own best guess, not LedgerBrug's real
contract — `ledgerbrug-pos-event-contract.md` was referenced in their
email but never actually reached us. Treat every field name and the auth
header format below as provisional until we get that document and
reconcile against it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import random

from sqlalchemy.orm import Session

from app.domain.integrations import LedgerBrugEventStatus, LedgerBrugOutboxEvent
from app.domain.orders import Order


def build_event_payload(order: Order, event_type: str) -> dict:
    """
    Our proposed shape, per what we told LedgerBrug's dev team we could
    support (per-line VAT rate, split payments with provider reference,
    a permanent receipt number). Subject to revision once their actual
    contract document is in hand.
    """
    return {
        "event_type": event_type,  # "order.completed" | "order.refunded" | "order.voided"
        "receipt_number": order.receipt_number,
        "order_id": order.id,
        "tenant_id": order.tenant_id,
        "store_id": order.store_id,
        "register_id": order.register_id,
        "cashier_user_id": order.cashier_user_id,
        "business_date": order.created_at.date().isoformat() if order.created_at else None,
        "currency": order.currency,
        "status": order.status.value,
        "subtotal_minor": order.subtotal_minor,
        "tax_minor": order.tax_minor,
        "total_minor": order.total_minor,
        "lines": [
            {
                "product_id": line.product_id,
                "quantity": line.quantity,
                "unit_price_minor": line.unit_price_minor,
                "tax_rate_basis_points": line.tax_rate_basis_points,
                "tax_minor": line.tax_minor,
                "line_total_minor": line.line_total_minor,
            }
            for line in order.lines
        ],
        "payments": [
            {
                "method": payment.method.value,
                "amount_minor": payment.amount_minor,
                "provider_reference": payment.provider_reference,
            }
            for payment in order.payments
        ],
        "voided_reason": order.voided_reason if event_type == "order.voided" else None,
    }


def enqueue_order_event(db: Session, tenant_id: int, order: Order, event_type: str) -> LedgerBrugOutboxEvent:
    event = LedgerBrugOutboxEvent(
        tenant_id=tenant_id,
        order_id=order.id,
        event_type=event_type,
        payload=build_event_payload(order, event_type),
        status=LedgerBrugEventStatus.PENDING,
        next_attempt_at=dt.datetime.utcnow(),
    )
    db.add(event)
    db.flush()
    return event


def compute_backoff_seconds(attempt_count: int, cap_seconds: float = 300.0) -> float:
    """Same shape as the Electron outbox's computeBackoffMs(): 2^n capped,
    with +/-20% jitter so many retrying events don't all wake up in the
    same instant."""
    base = min(2 ** max(attempt_count, 0), cap_seconds)
    jitter = base * random.uniform(-0.2, 0.2)
    return max(0.0, base + jitter)


def sign_payload(payload: dict, secret: str) -> str:
    """HMAC-SHA256 over the exact JSON bytes sent, hex-encoded. This is a
    reasonable, common default for webhook authenticity — LedgerBrug's
    actual contract may specify a different header name or scheme, which
    is exactly the kind of detail their contract doc will settle."""
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def send_pending_events(
    db: Session,
    http_post,
    webhook_url: str,
    webhook_secret: str,
    max_attempts: int = 10,
    limit: int = 50,
) -> dict:
    """
    `http_post(url, json_body, headers) -> status_code` is injected rather
    than imported (e.g. requests.post) so this function stays trivially
    testable without a real HTTP call or a running server on the other
    end — the same reason the Electron sync engine's own sender takes its
    transport as a parameter.

    Returns a small summary dict ({"sent": n, "failed": n, "still_pending": n})
    rather than raising on a per-event failure — one bad event must not
    stop the rest of the batch from being attempted.
    """
    now = dt.datetime.utcnow()
    events = (
        db.query(LedgerBrugOutboxEvent)
        .filter(
            LedgerBrugOutboxEvent.status == LedgerBrugEventStatus.PENDING,
            LedgerBrugOutboxEvent.next_attempt_at <= now,
        )
        .order_by(LedgerBrugOutboxEvent.id)
        .limit(limit)
        .all()
    )

    sent = failed = 0
    for event in events:
        signature = sign_payload(event.payload, webhook_secret)
        try:
            status_code = http_post(
                webhook_url,
                event.payload,
                {"X-LedgerBrug-Signature": signature, "Content-Type": "application/json"},
            )
        except Exception as exc:  # network error, timeout, etc.
            status_code = None
            error_text = str(exc)
        else:
            error_text = None if 200 <= status_code < 300 else f"HTTP {status_code}"

        if status_code is not None and 200 <= status_code < 300:
            event.status = LedgerBrugEventStatus.SENT
            event.sent_at = now
            sent += 1
        else:
            event.attempt_count += 1
            event.last_error = error_text
            if event.attempt_count >= max_attempts:
                event.status = LedgerBrugEventStatus.FAILED
                failed += 1
            else:
                event.next_attempt_at = now + dt.timedelta(
                    seconds=compute_backoff_seconds(event.attempt_count)
                )

    db.flush()
    still_pending = (
        db.query(LedgerBrugOutboxEvent).filter(LedgerBrugOutboxEvent.status == LedgerBrugEventStatus.PENDING).count()
    )
    return {"sent": sent, "failed": failed, "still_pending": still_pending}
