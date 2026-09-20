"""
Phase 4/7 — refund with a data-driven approval threshold (plan §52's
worked example: "cashier can refund up to €20, manager approval above").

The threshold itself is NOT hardcoded here — it's read from
ApprovalPolicy(action_code='orders.refund'). If no policy row exists for
the tenant, this defaults to requiring approval for every refund (fail
safe, not fail open) rather than silently allowing unlimited refunds.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.authz import Approval, ApprovalPolicy, AuditLog
from app.domain.orders import Order, OrderStatus, Refund


class RefundError(Exception):
    pass


class RefundRequiresApprovalError(RefundError):
    def __init__(self, action_code: str, threshold_minor_units: int | None):
        self.action_code = action_code
        self.threshold_minor_units = threshold_minor_units
        super().__init__(
            f"Refund requires manager approval (policy '{action_code}', "
            f"threshold={threshold_minor_units})"
        )


def _get_refund_threshold(db: Session, tenant_id: int) -> int | None:
    policy = (
        db.query(ApprovalPolicy)
        .filter(ApprovalPolicy.tenant_id == tenant_id, ApprovalPolicy.action_code == "orders.refund")
        .first()
    )
    if not policy:
        return 0  # fail-safe default: no policy configured -> everything needs approval
    return policy.threshold_minor_units


def process_refund(
    db: Session,
    tenant_id: int,
    order_id: int,
    requested_by_user_id: int,
    amount_minor: int,
    reason: str | None,
    is_manager_override: bool = False,
) -> Refund:
    order = db.get(Order, order_id)
    if not order or order.tenant_id != tenant_id:
        raise RefundError(f"Order {order_id} not found")
    if amount_minor <= 0 or amount_minor > order.total_minor:
        raise RefundError(f"Invalid refund amount {amount_minor} for order total {order.total_minor}")

    threshold = _get_refund_threshold(db, tenant_id)
    needs_approval = threshold is None or amount_minor > threshold

    if needs_approval and not is_manager_override:
        raise RefundRequiresApprovalError("orders.refund", threshold)

    refund = Refund(
        order_id=order.id,
        requested_by_user_id=requested_by_user_id,
        amount_minor=amount_minor,
        reason=reason,
    )
    db.add(refund)

    order.status = OrderStatus.REFUNDED if amount_minor == order.total_minor else OrderStatus.PARTIALLY_REFUNDED

    db.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=requested_by_user_id,
            action="order.refund",
            entity_type="order",
            entity_id=str(order.id),
            before={"status": "COMPLETED"},
            after={"status": order.status.value, "refund_amount_minor": amount_minor},
            reason=reason,
            source="POS",
        )
    )
    db.commit()
    db.refresh(refund)
    return refund
