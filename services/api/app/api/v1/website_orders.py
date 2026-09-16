"""
Phase 11 — Website Orders screen backend (plan §19-30).

This is the API the desktop app's "Website Orders" tab calls. It is a
proper domain/API integration (query + status-change + audit), not a
browser tab pointed at the website, per the plan's explicit architectural
requirement: "the data should be synchronized into the POS domain so that
searching, filtering, status transitions, offline queuing, auditing, and
reporting remain reliable."
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import require_permission
from app.core.security import Principal
from app.db.session import get_db
from app.domain.authz import AuditLog
from app.domain.website import WebsiteOrder, WebsiteOrderStatus, WebsiteOrderStatusHistory
from app.services.website_order_workflow import (
    InvalidTransitionError,
    assert_valid_transition,
    requires_approval,
)

router = APIRouter(prefix="/website-orders", tags=["website-orders"])


class WebsiteOrderOut(BaseModel):
    id: int
    external_order_number: str
    status: str
    customer_name: str | None
    payment_status: str
    delivery_method: str
    pending_website_sync: bool

    class Config:
        from_attributes = True


class PagedWebsiteOrders(BaseModel):
    items: list[WebsiteOrderOut]
    page: int
    page_size: int
    total: int


@router.get("", response_model=PagedWebsiteOrders)
def list_website_orders(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("website_orders.view")),
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
):
    """Server-side paginated (plan §28) — never load the whole table."""
    q = select(WebsiteOrder)
    if status_filter:
        q = q.where(WebsiteOrder.status == status_filter)
    if search:
        like = f"%{search}%"
        q = q.where(
            (WebsiteOrder.external_order_number.ilike(like))
            | (WebsiteOrder.customer_name.ilike(like))
            | (WebsiteOrder.customer_phone.ilike(like))
        )
    total = len(db.execute(q).scalars().all())
    q = q.order_by(WebsiteOrder.id.desc()).offset((page - 1) * page_size).limit(page_size)
    items = db.execute(q).scalars().all()
    return PagedWebsiteOrders(
        items=[WebsiteOrderOut.model_validate(i) for i in items], page=page, page_size=page_size, total=total
    )


class StatusChangeRequest(BaseModel):
    new_status: str
    reason: str | None = None


@router.post("/{website_order_id}/status")
def change_status(
    website_order_id: int,
    body: StatusChangeRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("website_orders.change_status")),
):
    wo = db.get(WebsiteOrder, website_order_id)
    if not wo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Website order not found")

    try:
        target = WebsiteOrderStatus(body.new_status)
        current = WebsiteOrderStatus(wo.status)
        assert_valid_transition(current, target)
    except (ValueError, InvalidTransitionError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if requires_approval(current, target):
        # Phase 4's Approval flow gates this instead of applying it inline —
        # not implemented in this endpoint yet; failing closed (403) rather
        # than silently allowing a sensitive transition is the correct
        # behavior until that wiring exists (plan §102: don't fabricate
        # a working integration).
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"{current.value} -> {target.value} requires manager approval (Approval workflow not yet wired to this endpoint)",
        )

    old_status = wo.status
    wo.status = target
    wo.pending_website_sync = True  # cleared by the sync worker once the website confirms the push

    db.add(
        WebsiteOrderStatusHistory(
            website_order_id=wo.id,
            old_status=old_status,
            new_status=target.value,
            changed_by_user_id=principal.user_id,
            source="POS",
            reason=body.reason,
        )
    )
    db.add(
        AuditLog(
            tenant_id=1,  # single-tenant default for now; Phase 13 threads tenant through the principal
            user_id=principal.user_id,
            action="website_order.status_change",
            entity_type="website_order",
            entity_id=str(wo.id),
            before={"status": old_status},
            after={"status": target.value},
            source="POS",
        )
    )
    db.commit()
    return {"status": "ok", "new_status": target.value, "pending_website_sync": True}
