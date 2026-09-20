"""Phase 6/7 checkout API — the piece the phase-status report flagged as
the largest missing gap. Wraps app.services.checkout / app.services.refunds."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.rbac import require_permission, principal_has_permission
from app.core.security import Principal
from app.db.session import get_db
from app.domain.orders import Order, PaymentMethod
from app.services.checkout import CartLineInput, CheckoutError, create_pos_sale
from app.services.refunds import RefundError, RefundRequiresApprovalError, process_refund

router = APIRouter(prefix="/orders", tags=["orders"])


class CartLineIn(BaseModel):
    product_id: int
    quantity: int


class CreateOrderRequest(BaseModel):
    register_id: int
    lines: list[CartLineIn]
    payment_method: PaymentMethod = PaymentMethod.CASH


class OrderOut(BaseModel):
    id: int
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
        order = create_pos_sale(
            db,
            tenant_id=principal.tenant_id,
            store_id=principal.store_id,
            register_id=body.register_id,
            cashier_user_id=principal.user_id,
            lines=[CartLineInput(l.product_id, l.quantity) for l in body.lines],
            payment_method=body.payment_method,
        )
    except CheckoutError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
    # The override flag bypasses the approval threshold entirely, so it
    # requires its own, separately-granted permission — never just
    # whatever the caller sends. A Cashier holding "orders.refund" cannot
    # self-approve by setting manager_override=true; only a role that
    # also has "orders.refund.override" (Owner/Administrator/Store
    # Manager in the seed data) can.
    effective_override = body.manager_override and principal_has_permission(db, principal, "orders.refund.override")

    try:
        refund = process_refund(
            db,
            tenant_id=principal.tenant_id,
            order_id=order_id,
            requested_by_user_id=principal.user_id,
            amount_minor=body.amount_minor,
            reason=body.reason,
            is_manager_override=effective_override,
        )
    except RefundRequiresApprovalError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "message": str(exc),
                "action_code": exc.action_code,
                "threshold_minor_units": exc.threshold_minor_units,
            },
        ) from exc
    except RefundError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return {"status": "ok", "refund_id": refund.id, "amount_minor": refund.amount_minor}
