"""
Phase 9B — discount authorization, applied BEFORE a sale is created (not
after, like refunds — a discount is a decision about what to charge, not
a correction to an already-completed charge).

Threshold is data-driven, same ApprovalPolicy(action_code=...) pattern
process_refund already uses (plan §52/§53: "thresholds live in data,
never hardcoded"), with the same fail-safe default (no policy configured
-> every discount needs approval, not "everything's free"). A Cashier can
apply a discount up to the threshold on their own (`orders.discount.apply`
+ within-threshold); a discount above it requires either an
`orders.discount.override` holder applying it directly (the "manager did
this themselves at the register" case) or a separate manager resolving a
pending approval a cashier's over-threshold request created — the exact
same two-path shape as refunds' `manager_override_requested` /
`resolve_approval` pair, so the same acceptance and audit trail exist for
both financial-adjustment features rather than two different mental
models a cashier has to learn.

Unlike refunds, there is no in-progress `Order` row when a discount
approval is requested — the sale hasn't happened yet — so a REQUESTED
discount Approval stores the FULL cart (lines/customer/payment) in its
`context` JSON, and `resolve_discount_approval()` is what actually calls
create_pos_sale() for the first time, on approval.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.core.rbac import principal_has_permission
from app.core.security import Principal
from app.domain.authz import Approval, ApprovalPolicy, AuditLog
from app.domain.orders import Order, PaymentMethod
from app.services.checkout import CartLineInput, CheckoutError, PaymentInput, create_pos_sale


class DiscountError(Exception):
    pass


class DiscountRequiresApprovalError(DiscountError):
    def __init__(self, threshold_minor_units: int | None, approval_id: int):
        self.threshold_minor_units = threshold_minor_units
        self.approval_id = approval_id
        super().__init__(
            f"Discount requires manager approval (threshold={threshold_minor_units}, approval_id={approval_id})"
        )


class DiscountApprovalError(DiscountError):
    pass


def _get_discount_threshold(db: Session, tenant_id: int) -> int | None:
    policy = (
        db.query(ApprovalPolicy)
        .filter(ApprovalPolicy.tenant_id == tenant_id, ApprovalPolicy.action_code == "orders.discount")
        .first()
    )
    if not policy:
        return 0  # fail-safe default: no policy configured -> everything needs approval
    return policy.threshold_minor_units


def _cart_context(
    tenant_id: int, store_id: int, register_id: int, cashier_user_id: int,
    lines: list[CartLineInput], payment_method: PaymentMethod | None, payments: list | None,
    currency: str, customer_id: int | None,
) -> dict:
    return {
        "tenant_id": tenant_id,
        "store_id": store_id,
        "register_id": register_id,
        "cashier_user_id": cashier_user_id,
        "customer_id": customer_id,
        "currency": currency,
        "payment_method": payment_method.value if payment_method else None,
        "payments": [{"method": p.method.value, "amount_minor": p.amount_minor, "provider_reference": p.provider_reference} for p in payments] if payments else None,
        "lines": [{"product_id": l.product_id, "quantity": l.quantity, "discount_minor": l.discount_minor} for l in lines],
        "total_discount_minor": sum(l.discount_minor for l in lines),
    }


def checkout_with_discount_check(
    db: Session,
    tenant_id: int,
    store_id: int,
    register_id: int,
    cashier_user_id: int,
    principal: Principal,
    lines: list[CartLineInput],
    payment_method: PaymentMethod | None = None,
    payments: list[PaymentInput] | None = None,
    currency: str = "EUR",
    customer_id: int | None = None,
    manager_override_requested: bool = False,
) -> Order:
    """
    The gate every checkout call goes through (whether or not it actually
    contains a discount — a cart with zero discount always sails through
    with no approval overhead). Mirrors process_refund()'s shape: checks
    the threshold, checks the requesting principal's OWN permissions
    (never a caller-supplied boolean — same finding #14 discipline), and
    either proceeds to create_pos_sale() directly or raises
    DiscountRequiresApprovalError with a real, queryable Approval row a
    manager can act on.
    """
    total_discount_minor = sum(l.discount_minor for l in lines)

    if total_discount_minor <= 0:
        return create_pos_sale(
            db, tenant_id, store_id, register_id, cashier_user_id, lines,
            payment_method=payment_method, payments=payments, currency=currency, customer_id=customer_id,
        )

    if not principal_has_permission(db, principal, "orders.discount.apply") and not principal_has_permission(
        db, principal, "orders.discount.override"
    ):
        raise DiscountApprovalError("This role is not permitted to apply a discount")

    threshold = _get_discount_threshold(db, tenant_id)
    needs_approval = threshold is None or total_discount_minor > threshold

    effective_override = manager_override_requested and principal_has_permission(
        db, principal, "orders.discount.override"
    )
    # A discount-override HOLDER applying it themselves (no separate
    # "requested" step needed — this IS the manager, at the register).
    self_authorized = principal_has_permission(db, principal, "orders.discount.override")

    if needs_approval and not effective_override and not self_authorized:
        approval = Approval(
            tenant_id=tenant_id,
            action_code="orders.discount",
            requested_by_user_id=principal.user_id,
            status="REQUESTED",
            context=_cart_context(
                tenant_id, store_id, register_id, cashier_user_id, lines,
                payment_method, payments, currency, customer_id,
            ),
        )
        db.add(approval)
        db.flush()
        raise DiscountRequiresApprovalError(threshold, approval_id=approval.id)

    order = create_pos_sale(
        db, tenant_id, store_id, register_id, cashier_user_id, lines,
        payment_method=payment_method, payments=payments, currency=currency, customer_id=customer_id,
    )
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=cashier_user_id,
            action="order.discount_applied",
            entity_type="order",
            entity_id=str(order.id),
            after={"total_discount_minor": total_discount_minor, "threshold": threshold},
            source="POS",
        )
    )
    db.flush()
    return order


def resolve_discount_approval(
    db: Session,
    tenant_id: int,
    approval_id: int,
    resolver: Principal,
    approve: bool,
) -> tuple[Approval, Order | None]:
    """
    Executes (or rejects) a previously-REQUESTED discount approval. On
    approve, this is the FIRST time create_pos_sale() runs for this cart
    — nothing was created at request time, unlike a refund (which already
    has an Order to act on). Re-checks "orders.discount.override" on the
    resolver independently, same reasoning as resolve_approval() in
    refunds.py.
    """
    approval = db.get(Approval, approval_id)
    if not approval or approval.tenant_id != tenant_id:
        raise DiscountApprovalError(f"Approval {approval_id} not found")
    if approval.action_code != "orders.discount":
        raise DiscountApprovalError(f"Approval {approval_id} is not a discount approval")
    if approval.status != "REQUESTED":
        raise DiscountApprovalError(f"Approval {approval_id} is already {approval.status}, not REQUESTED")
    if not principal_has_permission(db, resolver, "orders.discount.override"):
        raise DiscountApprovalError("Resolving a discount approval requires the orders.discount.override permission")

    if not approve:
        approval.status = "REJECTED"
        approval.approved_by_user_id = resolver.user_id
        approval.resolved_at = dt.datetime.utcnow()
        db.add(
            AuditLog(
                tenant_id=tenant_id, user_id=resolver.user_id, action="approval.reject",
                entity_type="approval", entity_id=str(approval.id), after={"status": "REJECTED"}, source="POS",
            )
        )
        db.flush()
        return approval, None

    ctx = approval.context
    lines = [CartLineInput(l["product_id"], l["quantity"], l.get("discount_minor", 0)) for l in ctx["lines"]]
    payment_method = PaymentMethod(ctx["payment_method"]) if ctx.get("payment_method") else None
    payments = (
        [PaymentInput(PaymentMethod(p["method"]), p["amount_minor"], p.get("provider_reference")) for p in ctx["payments"]]
        if ctx.get("payments")
        else None
    )

    try:
        order = create_pos_sale(
            db, ctx["tenant_id"], ctx["store_id"], ctx["register_id"], ctx["cashier_user_id"], lines,
            payment_method=payment_method, payments=payments, currency=ctx["currency"],
            customer_id=ctx.get("customer_id"),
        )
    except CheckoutError as exc:
        # The cart may no longer be valid by the time a manager gets to
        # it (a product was deleted, the session closed, etc.) — surface
        # that clearly rather than silently leaving the approval REQUESTED
        # forever. The approval itself is left REQUESTED (not consumed)
        # so it's still visible for the manager to see it failed and
        # decide what to do — a swallowed failure here would be a
        # discount someone approved that silently never happened.
        raise DiscountApprovalError(f"Approved discount could no longer be applied: {exc}") from exc

    approval.status = "APPROVED"
    approval.approved_by_user_id = resolver.user_id
    approval.resolved_at = dt.datetime.utcnow()
    approval.context = {**ctx, "order_id": order.id}
    db.add(
        AuditLog(
            tenant_id=tenant_id, user_id=resolver.user_id, action="approval.approve",
            entity_type="approval", entity_id=str(approval.id),
            after={"status": "APPROVED", "order_id": order.id, "total_discount_minor": ctx["total_discount_minor"]},
            source="POS",
        )
    )
    db.flush()
    return approval, order
