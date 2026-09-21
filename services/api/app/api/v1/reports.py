"""
Phase 22 — X/Z day-close report (LedgerBrug dev-team question #5).

GET is the X-report: computed on demand, never persisted, gated on the
existing `reports.view` permission (same access level as every other
report in this API). POST is the Z-report: the same computation,
persisted exactly once per (tenant, store, business_date), gated on the
new `reports.day_close.finalize` permission — deliberately narrower than
`reports.view`, since finalizing the day's books is a manager-level
action, not a look-up.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.rbac import require_permission
from app.core.security import Principal
from app.db.session import get_db
from app.services.reports import DayCloseAlreadyFinalizedError, DayCloseError, build_day_close

router = APIRouter(prefix="/reports", tags=["reports"])


class DayCloseOut(BaseModel):
    tenant_id: int
    store_id: int
    business_date: dt.date
    is_finalized: bool
    total_sales_minor: int
    total_tax_minor: int
    total_refunds_minor: int
    total_voided_minor: int
    voided_count: int
    receipt_count: int
    first_receipt_number: str | None
    last_receipt_number: str | None
    vat_breakdown: list[dict]
    payment_breakdown: list[dict]


@router.get("/day-close", response_model=DayCloseOut)
def get_x_report(
    store_id: int = Query(...),
    business_date: dt.date = Query(...),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("reports.view")),
):
    """X-report — a mid-shift or after-the-fact read, safe to call any
    number of times; never writes anything."""
    result = build_day_close(db, tenant_id=principal.tenant_id, store_id=store_id, business_date=business_date, finalize=False)
    return result


class FinalizeDayCloseRequest(BaseModel):
    store_id: int
    business_date: dt.date


@router.post("/day-close", response_model=DayCloseOut)
def finalize_z_report(
    body: FinalizeDayCloseRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("reports.day_close.finalize")),
):
    """Z-report — finalizes and persists exactly one closing record per
    (tenant, store, business_date). Refuses to run twice for the same
    day, on purpose (see app/services/reports.py)."""
    try:
        result = build_day_close(
            db,
            tenant_id=principal.tenant_id,
            store_id=body.store_id,
            business_date=body.business_date,
            finalize=True,
            closed_by_user_id=principal.user_id,
        )
        db.commit()
    except DayCloseAlreadyFinalizedError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DayCloseError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return result
