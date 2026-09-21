"""Phase 6/7 checkout API — the piece the phase-status report flagged as
the largest missing gap. Wraps app.services.checkout / app.services.refunds."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.rbac import require_permission
from app.core.security import Principal
from app.db.session import get_db
from app.domain.orders import Order, PaymentMethod
from app.services.checkout import CartLineInput, CheckoutError, PaymentInput, UnauthorizedResourceError, create_pos_sale
from app.services.ledgerbrug import enqueue_order_event
from app.services.refunds import RefundError, RefundRequiresApprovalError, process_refund
from app.services.voids import VoidError, VoidNotPermittedError, void_order

router = APIRouter(prefix="/orders", tags=["orders"])


class CartLineIn(BaseModel):
    product_id: int
    quantity: int


class PaymentIn(BaseModel):
    """Phase 22 — one tender on a split payment. `provider_reference` is
    the PSP/card-network transaction id, when the caller has one at
    checkout time (often it won't yet, and can be filled in later once
    the terminal settles — left optional rather than required)."""

    method: PaymentMethod
    amount_minor: int
    provider_reference: str | None = None


class CreateOrderRequest(BaseModel):
    register_id: int
    lines: list[CartLineIn]
    # Exactly one of these two. `payment_method` is the pre-Phase-22 single
    # -tender shape, kept as the default so existing POS/Electron clients
    # keep working unchanged; `payments` is the new split-payment path
    # (Phase 22, LedgerBrug dev-team question #2). If `payments` is given,
    # `payment_method` is ignored.
    payment_method: PaymentMethod = PaymentMethod.CASH
    payments: list[PaymentIn] | None = None


class OrderOut(BaseModel):
    id: int
    receipt_number: str | None
    status: str
    subtotal_minor: int
    tax_minor: int
    discount_minor: int
    total_minor: int
    currency: str

    class Config:
        from_attributes = True


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(
    body: CreateOrderRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("orders.create")),
):
    if principal.store_id is None:
        # Was previously masked by `principal.store_id or 1` silently
        # falling back to a hardcoded store — that's a real bug (a user
        # with no assigned store must not have their sale attributed to
        # store 1 by accident), so this now fails loudly instead.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="This user has no store assigned — cannot ring up a sale without a store context",
        )
    try:
        if body.payments is not None:
            order = create_pos_sale(
                db,
                tenant_id=principal.tenant_id,
                store_id=principal.store_id,
                register_id=body.register_id,
                cashier_user_id=principal.user_id,
                lines=[CartLineInput(l.product_id, l.quantity) for l in body.lines],
                payments=[PaymentInput(p.method, p.amount_minor, p.provider_reference) for p in body.payments],
            )
        else:
            order = create_pos_sale(
                db,
                tenant_id=principal.tenant_id,
                store_id=principal.store_id,
                register_id=body.register_id,
                cashier_user_id=principal.user_id,
                lines=[CartLineInput(l.product_id, l.quantity) for l in body.lines],
                payment_method=body.payment_method,
            )
        # Phase 22: queue the LedgerBrug event in the SAME transaction as
        # the order, so it is never possible for one to be committed
        # without the other (see app/services/ledgerbrug.py).
        enqueue_order_event(db, principal.tenant_id, order, "order.completed")
        # Transaction ownership lives here now (CTO audit finding #17):
        # create_pos_sale only flushes. This route is the single
        # transaction boundary for a direct POS sale.
        db.commit()
        db.refresh(order)
    except UnauthorizedResourceError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except CheckoutError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return order


@router.get("/by-receipt/{receipt_number}", response_model=OrderOut)
def get_order_by_receipt(
    receipt_number: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("orders.create")),
):
    """
    Phase 22 (LedgerBrug dev-team question #3): the cashier-facing lookup
    — scan the barcode on a printed receipt, find the sale. Registered
    ahead of GET /{order_id} so "by-receipt" is never swallowed as an
    order_id path parameter.
    """
    order = db.query(Order).filter(Order.receipt_number == receipt_number, Order.tenant_id == principal.tenant_id).first()
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No order with that receipt number")
    return order


@router.get("/{order_id}", response_model=OrderOut)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("orders.create")),
):
    order = db.get(Order, order_id)
    # Tenant check: previously missing entirely, so any authenticated user
    # of ANY tenant could read any other tenant's order by guessing an id.
    if not order or order.tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


class RefundRequest(BaseModel):
    amount_minor: int
    reason: str | None = None
    manager_override: bool = False


@router.post("/{order_id}/refund")
def refund_order(
    order_id: int,
    body: RefundRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("orders.refund")),
):
    # process_refund itself now independently checks
    # "orders.refund.override" against `principal` (CTO audit finding #14
    # — a critical financial invariant should not depend entirely on the
    # route remembering to gate it). This route just passes the caller's
    # request through; it no longer pre-computes an "effective override"
    # the service is expected to trust.
    try:
        refund = process_refund(
            db,
            tenant_id=principal.tenant_id,
            order_id=order_id,
            principal=principal,
            amount_minor=body.amount_minor,
            reason=body.reason,
            manager_override_requested=body.manager_override,
        )
        order = db.get(Order, order_id)
        enqueue_order_event(db, principal.tenant_id, order, "order.refunded")
        db.commit()
        db.refresh(refund)
    except RefundRequiresApprovalError as exc:
        # A pending Approval row now actually exists (finding #13) — its
        # id is returned so the UI/manager flow has something concrete to
        # act on, not just an error message.
        db.commit()  # persist the Approval row created before this was raised
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "message": str(exc),
                "action_code": exc.action_code,
                "threshold_minor_units": exc.threshold_minor_units,
                "approval_id": exc.approval_id,
            },
        ) from exc
    except RefundError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return {"status": "ok", "refund_id": refund.id, "amount_minor": refund.amount_minor}


class VoidRequest(BaseModel):
    reason: str | None = None


@router.post("/{order_id}/void", response_model=OrderOut)
def void_order_endpoint(
    order_id: int,
    body: VoidRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("orders.void")),
):
    """
    Phase 22 (LedgerBrug dev-team question #4's second half). Distinct
    from /refund: same-day only, reverses inventory, keeps the original
    receipt_number. See app/services/voids.py for the full rationale.
    """
    try:
        order = void_order(db, tenant_id=principal.tenant_id, order_id=order_id, principal=principal, reason=body.reason)
        enqueue_order_event(db, principal.tenant_id, order, "order.voided")
        db.commit()
        db.refresh(order)
    except VoidNotPermittedError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except VoidError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return order
