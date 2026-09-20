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

from app.core.rbac import require_permission
from app.core.security import Principal
from app.db.session import get_db
from app.domain.authz import Approval
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


@router.get("", response_model=list[ApprovalOut])
def list_approvals(
    status_filter: str | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("orders.refund.override")),
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
    principal: Principal = Depends(require_permission("orders.refund.override")),
):
    try:
        approval = resolve_approval(
            db,
            tenant_id=principal.tenant_id,
            approval_id=approval_id,
            resolver=principal,
            approve=body.approve,
        )
        db.commit()
        db.refresh(approval)
    except (ApprovalError, RefundError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return approval
