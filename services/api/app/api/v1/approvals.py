"""
Phase 8.5 — the manager-facing half of the real approval workflow (CTO
audit of 0cfd8ca, finding #13: "the actual refund flow doesn't create a
pending Approval when a refund exceeds the threshold ... this is currently
'Approval required' rather than 'Approval workflow'").

Cashier requests refund -> Approval REQUESTED (app/services/refunds.py)
    -> Manager lists pending approvals (GET /approvals)
    -> Manager approves/rejects (POST /approvals/{id}/resolve)
    -> Refund actually executes on approval, or nothing happens on reject
    -> Audit event either way

Gated on "orders.refund.override" — the same permission that lets a
manager bypass the threshold directly — because resolving a pending
approval IS the manager override action, just applied after the fact
instead of at request time.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.rbac import get_current_principal, principal_has_permission
from app.core.security import Principal
from app.db.session import get_db
from app.domain.authz import Approval
from app.domain.orders import Order
from app.services.discounts import DiscountApprovalError, resolve_discount_approval
from app.services.ledgerbrug import enqueue_order_event
from app.services.refunds import ApprovalError, RefundError, resolve_approval

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalOut(BaseModel):
    id: int
    action_code: str
    requested_by_user_id: int
    approved_by_user_id: int | None
    status: str
    context: dict

    class Config:
        from_attributes = True


def _require_any_override_permission(principal: Principal = Depends(get_current_principal)) -> Principal:
    """
    Phase 9B: approvals now cover two action codes ("orders.refund" and
    "orders.discount") gated on two DIFFERENT override permissions. A
    single-permission require_permission() dependency can't express "has
    EITHER of these", so this checks both explicitly — a manager who can
    resolve refund approvals but was deliberately not given discount
    authority (or vice versa) still can't act outside their own scope;
    resolve_approval()/resolve_discount_approval() each independently
    re-check the SPECIFIC permission for the approval's own action_code
    regardless of what got the caller through this door, so this gate is
    strictly a "can they see the list at all" check, not the authority
    check itself.
    """
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        if not principal_has_permission(db, principal, "orders.refund.override") and not principal_has_permission(
            db, principal, "orders.discount.override"
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=f"Role '{principal.role}' lacks orders.refund.override or orders.discount.override",
            )
    return principal


@router.get("", response_model=list[ApprovalOut])
def list_approvals(
    status_filter: str | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(_require_any_override_permission),
):
    query = db.query(Approval).filter(Approval.tenant_id == principal.tenant_id)
    if status_filter:
        query = query.filter(Approval.status == status_filter)
    return query.order_by(Approval.created_at.desc()).all()


class ResolveApprovalRequest(BaseModel):
    approve: bool


@router.post("/{approval_id}/resolve", response_model=ApprovalOut)
def resolve_approval_route(
    approval_id: int,
    body: ResolveApprovalRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(_require_any_override_permission),
):
    """
    Dispatches on the approval's own action_code — a discount approval
    and a refund approval are resolved through two different services
    (discounts.py hasn't got an Order to act on yet; refunds.py already
    does), but a manager sees and resolves both from the same list/route
    rather than needing to know in advance which kind an id refers to.
    """
    approval = db.get(Approval, approval_id)
    if not approval or approval.tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Approval {approval_id} not found")

    try:
        if approval.action_code == "orders.discount":
            approval, order = resolve_discount_approval(
                db, tenant_id=principal.tenant_id, approval_id=approval_id, resolver=principal, approve=body.approve,
            )
            if body.approve and order is not None:
                enqueue_order_event(db, principal.tenant_id, order, "order.completed")
        else:
            approval = resolve_approval(
                db,
                tenant_id=principal.tenant_id,
                approval_id=approval_id,
                resolver=principal,
                approve=body.approve,
            )
            if body.approve and approval.status == "APPROVED":
                # Phase 22: this is the point a manager-approved refund
                # actually executes — enqueue the LedgerBrug event here, in
                # the same transaction, same as the direct-refund path in
                # orders.py.
                order = db.get(Order, approval.context["order_id"])
                enqueue_order_event(db, principal.tenant_id, order, "order.refunded")
        db.commit()
        db.refresh(approval)
    except (ApprovalError, RefundError, DiscountApprovalError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return approval
