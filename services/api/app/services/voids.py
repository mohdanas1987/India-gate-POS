"""
Phase 22 — void (LedgerBrug dev-team question #4's second half).

Refund and void are different operations answering different questions:
a refund is "the customer already left with the goods, money is going
back" (settled sale, handled by app/services/refunds.py, and solid). A
void is "this receipt should never have existed — same-day cashier
mistake, caught before it settles." OrderStatus.VOIDED existed as an enum
value with nothing behind it before this file.

A void, unlike a refund:
  - reverses the inventory ledger (the goods never left, so stock comes
    back — a refund does NOT do this today, a separate disclosed gap
    noted in PHASE-STATUS.md, not something this file silently fixes by
    accident),
  - keeps the original receipt_number unchanged (the dev team's explicit
    requirement — LedgerBrug needs to see "receipt 00000042 was voided",
    not have that number disappear or get reused),
  - is restricted to the same business day as the sale, so it cannot be
    used as a delayed, unapproved substitute for the refund+approval
    workflow.

Locking follows the same discipline as refunds.py's
`_lock_order_for_refund` — SELECT ... FOR UPDATE before the state check,
so a concurrent void and refund attempt against the same order cannot
race each other into an inconsistent status.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.core.rbac import principal_has_permission
from app.core.security import Principal
from app.domain.authz import AuditLog
from app.domain.inventory import InventoryLedger, LedgerEventType
from app.domain.orders import Order, OrderStatus


class VoidError(Exception):
    pass


class VoidNotPermittedError(VoidError):
    pass


def _lock_order_for_void(db: Session, tenant_id: int, order_id: int) -> Order:
    order = db.query(Order).filter(Order.id == order_id).with_for_update().first()
    if not order or order.tenant_id != tenant_id:
        raise VoidError(f"Order {order_id} not found")
    return order


def void_order(
    db: Session,
    tenant_id: int,
    order_id: int,
    principal: Principal,
    reason: str | None = None,
    now: dt.datetime | None = None,
) -> Order:
    # Independent domain-layer permission check (same pattern as
    # process_refund, Phase 8.5 finding #14) — this must not depend
    # entirely on the route remembering to gate it correctly.
    if not principal_has_permission(db, principal, "orders.void"):
        raise VoidNotPermittedError("Caller does not hold orders.void")

    now = now or dt.datetime.utcnow()
    order = _lock_order_for_void(db, tenant_id, order_id)

    if order.status != OrderStatus.COMPLETED:
        raise VoidError(f"Order {order_id} has status {order.status.value} and cannot be voided")

    # Same-business-day only. A mistake caught the next day (or later) is
    # a refund, not a void — voiding a settled sale from a prior day would
    # let it slip past the refund-approval threshold entirely.
    if order.created_at.date() != now.date():
        raise VoidError(
            f"Order {order_id} was created on {order.created_at.date()}, not today "
            f"({now.date()}) — a sale from a prior day must be refunded, not voided"
        )

    for line in order.lines:
        db.add(
            InventoryLedger(
                tenant_id=tenant_id,
                store_id=order.store_id,
                product_id=line.product_id,
                event_type=LedgerEventType.VOID,
                # Reversal of the original SALE's negative delta — the
                # goods never left, so stock comes back.
                quantity_delta=line.quantity,
                reference_type="order",
                reference_id=str(order.id),
            )
        )

    order.status = OrderStatus.VOIDED
    order.voided_by_user_id = principal.user_id
    order.voided_reason = reason
    order.voided_at = now
    # receipt_number is deliberately left untouched — the dev team's
    # explicit requirement is that a void keeps the original number.

    db.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=principal.user_id,
            action="order.void",
            entity_type="order",
            entity_id=str(order.id),
            after={"reason": reason, "receipt_number": order.receipt_number},
            source="POS",
        )
    )

    db.flush()
    return order
