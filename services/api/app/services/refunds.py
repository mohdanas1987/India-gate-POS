"""
Phase 4/7 — refund with a data-driven approval threshold (plan §52's
worked example: "cashier can refund up to €20, manager approval above").

The threshold itself is NOT hardcoded here — it's read from
ApprovalPolicy(action_code='orders.refund'). If no policy row exists for
the tenant, this defaults to requiring approval for every refund (fail
safe, not fail open) rather than silently allowing unlimited refunds.

Phase 8.5 rework (CTO audit of 0cfd8ca, findings #12/#13/#14 — two of
which were classified RELEASE BLOCKER):

  - #12 (cumulative refund limit): the previous version only checked
    `amount_minor <= order.total_minor` on EACH refund in isolation, so
    two separate €80 refunds against a €100 order each passed that check
    individually and together refunded €160 — impossible money. This
    version computes `remaining_refundable = order.total_minor - SUM(all
    previous non-rejected refunds)` and requires the new amount to fit
    inside what's left.

  - #12 (atomic double-refund protection): computing "remaining" and then
    inserting a new Refund row is a check-then-act race — two concurrent
    refund requests against the same order could both read the same
    "remaining" value before either commits. The order row is now locked
    with SELECT ... FOR UPDATE before that calculation, so a second
    concurrent refund against the same order blocks until the first
    transaction commits or rolls back, and then sees the up-to-date total.

  - #13 (real approval workflow, not just a 403): a refund that needs
    approval used to simply raise an error back to the API, which the
    route turned into an HTTP 403 — "approval required" in name only,
    with no actual pending-approval record a manager could see and act
    on. Now an Approval row (REQUESTED) is created and its id is exposed
    on RefundRequiresApprovalError, and a separate `resolve_approval()`
    function (called from app/api/v1/approvals.py) is what actually
    executes the refund once a manager approves it.

  - #14 (authorization belongs in the domain layer, not just the route):
    the override boolean used to be trusted as passed in by the caller.
    This service now takes the actual Principal and checks
    "orders.refund.override" itself via principal_has_permission — the
    route no longer independently pre-computes an "effective_override"
    the service just believes.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.rbac import principal_has_permission
from app.core.security import Principal
from app.domain.authz import Approval, ApprovalPolicy, AuditLog
from app.domain.orders import Order, OrderStatus, Refund


class RefundError(Exception):
    pass


class RefundRequiresApprovalError(RefundError):
    def __init__(self, action_code: str, threshold_minor_units: int | None, approval_id: int | None = None):
        self.action_code = action_code
        self.threshold_minor_units = threshold_minor_units
        self.approval_id = approval_id
        super().__init__(
            f"Refund requires manager approval (policy '{action_code}', "
            f"threshold={threshold_minor_units}, approval_id={approval_id})"
        )


class ApprovalError(RefundError):
    pass


def _get_refund_threshold(db: Session, tenant_id: int) -> int | None:
    policy = (
        db.query(ApprovalPolicy)
        .filter(ApprovalPolicy.tenant_id == tenant_id, ApprovalPolicy.action_code == "orders.refund")
        .first()
    )
    if not policy:
        return 0  # fail-safe default: no policy configured -> everything needs approval
    return policy.threshold_minor_units


def _lock_order_for_refund(db: Session, tenant_id: int, order_id: int) -> Order:
    """
    SELECT ... FOR UPDATE on the order row. This is what makes the
    remaining-refundable calculation below atomic under concurrency: a
    second refund request against the same order will block here until
    the first transaction commits (releasing the lock) or rolls back, at
    which point it re-reads the up-to-date sum of refunds rather than a
    stale value read before the first refund existed.
    """
    order = db.query(Order).filter(Order.id == order_id).with_for_update().first()
    if not order or order.tenant_id != tenant_id:
        raise RefundError(f"Order {order_id} not found")
    return order


def _remaining_refundable(db: Session, order: Order) -> int:
    already_refunded = (
        db.query(func.coalesce(func.sum(Refund.amount_minor), 0)).filter(Refund.order_id == order.id).scalar()
    )
    return order.total_minor - int(already_refunded or 0)


def process_refund(
    db: Session,
    tenant_id: int,
    order_id: int,
    principal: Principal,
    amount_minor: int,
    reason: str | None,
    manager_override_requested: bool = False,
) -> Refund:
    order = _lock_order_for_refund(db, tenant_id, order_id)

    if amount_minor <= 0:
        raise RefundError(f"Invalid refund amount {amount_minor}")

    remaining = _remaining_refundable(db, order)
    if amount_minor > remaining:
        raise RefundError(
            f"Refund amount {amount_minor} exceeds remaining refundable balance {remaining} "
            f"(order total {order.total_minor}, already refunded {order.total_minor - remaining})"
        )

    threshold = _get_refund_threshold(db, tenant_id)
    needs_approval = threshold is None or amount_minor > threshold

    # Domain-layer authorization (finding #14): this service decides
    # whether the override is actually valid by checking the requesting
    # principal's own permissions — it does not trust a boolean the route
    # computed and passed in.
    effective_override = manager_override_requested and principal_has_permission(
        db, principal, "orders.refund.override"
    )

    if needs_approval and not effective_override:
        approval = Approval(
            tenant_id=tenant_id,
            store_id=order.store_id,
            action_code="orders.refund",
            requested_by_user_id=principal.user_id,
            status="REQUESTED",
            context={
                "order_id": order_id,
                "amount_minor": amount_minor,
                "reason": reason,
                "requested_by_user_id": principal.user_id,
            },
        )
        db.add(approval)
        db.flush()
        raise RefundRequiresApprovalError("orders.refund", threshold, approval_id=approval.id)

    refund = _apply_refund(db, tenant_id, order, principal.user_id, amount_minor, reason, approval_id=None)
    return refund


def _apply_refund(
    db: Session,
    tenant_id: int,
    order: Order,
    requested_by_user_id: int,
    amount_minor: int,
    reason: str | None,
    approval_id: int | None,
) -> Refund:
    refund = Refund(
        order_id=order.id,
        requested_by_user_id=requested_by_user_id,
        approval_id=approval_id,
        amount_minor=amount_minor,
        reason=reason,
    )
    db.add(refund)

    remaining_after = _remaining_refundable(db, order) - amount_minor
    order.status = OrderStatus.REFUNDED if remaining_after <= 0 else OrderStatus.PARTIALLY_REFUNDED

    db.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=requested_by_user_id,
            action="order.refund",
            entity_type="order",
            entity_id=str(order.id),
            before={"status": "COMPLETED"},
            after={"status": order.status.value, "refund_amount_minor": amount_minor, "approval_id": approval_id},
            reason=reason,
            source="POS",
        )
    )
    # Flush, don't commit — see checkout.py's transaction-ownership note.
    # The caller (the route, or resolve_approval below) owns the commit.
    db.flush()
    return refund


def resolve_approval(
    db: Session,
    tenant_id: int,
    approval_id: int,
    resolver: Principal,
    approve: bool,
) -> Approval:
    """
    Executes (or rejects) a previously-REQUESTED refund approval. This is
    the second half of the real approval workflow the CTO audit asked
    for: a manager can only get here through app/api/v1/approvals.py,
    which itself requires "orders.refund.override" — but this function
    independently re-checks that permission on `resolver` rather than
    trusting the route, for the same reason process_refund re-checks it
    for manager_override_requested above: a critical financial invariant
    should not depend entirely on the API layer remembering to gate it.
    """
    approval = db.get(Approval, approval_id)
    if not approval or approval.tenant_id != tenant_id:
        raise ApprovalError(f"Approval {approval_id} not found")
    if approval.status != "REQUESTED":
        raise ApprovalError(f"Approval {approval_id} is already {approval.status}, not REQUESTED")
    if not principal_has_permission(db, resolver, "orders.refund.override"):
        raise ApprovalError("Resolving a refund approval requires the orders.refund.override permission")
    # Phase 9B correction gate (CTO review of 96f6aa9, security issue #2):
    # same store-scope re-derivation as discounts.py's
    # resolve_discount_approval — a store-scoped manager cannot resolve
    # another store's refund approval either, now that both action codes
    # share the same Approval table and this same gap.
    if (
        approval.store_id is not None
        and resolver.store_id is not None
        and approval.store_id != resolver.store_id
        and not principal_has_permission(db, resolver, "approvals.manage.all_stores")
    ):
        raise ApprovalError(
            f"Approval {approval_id} belongs to a different store than the resolver is authorized for"
        )

    import datetime as dt

    if not approve:
        approval.status = "REJECTED"
        approval.approved_by_user_id = resolver.user_id
        approval.resolved_at = dt.datetime.utcnow()
        db.add(
            AuditLog(
                tenant_id=tenant_id,
                user_id=resolver.user_id,
                action="approval.reject",
                entity_type="approval",
                entity_id=str(approval.id),
                after={"status": "REJECTED"},
                source="POS",
            )
        )
        db.flush()
        return approval

    order_id = approval.context["order_id"]
    amount_minor = approval.context["amount_minor"]
    reason = approval.context.get("reason")
    requested_by_user_id = approval.context["requested_by_user_id"]

    order = _lock_order_for_refund(db, tenant_id, order_id)
    remaining = _remaining_refundable(db, order)
    if amount_minor > remaining:
        # The order changed shape between request and approval (e.g. a
        # different refund was approved in the meantime) — re-validate
        # rather than blindly trusting the amount captured at request time.
        raise RefundError(
            f"Refund amount {amount_minor} no longer fits the remaining refundable balance {remaining} "
            f"for order {order_id} — it may have changed since this approval was requested"
        )

    _apply_refund(db, tenant_id, order, requested_by_user_id, amount_minor, reason, approval_id=approval.id)

    approval.status = "APPROVED"
    approval.approved_by_user_id = resolver.user_id
    approval.resolved_at = dt.datetime.utcnow()
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=resolver.user_id,
            action="approval.approve",
            entity_type="approval",
            entity_id=str(approval.id),
            after={"status": "APPROVED", "order_id": order_id, "amount_minor": amount_minor},
            source="POS",
        )
    )
    db.flush()
    return approval
