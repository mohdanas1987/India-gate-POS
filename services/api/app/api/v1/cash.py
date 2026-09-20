"""Cashier session open/close (Phase 6/7 checkout precondition)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.rbac import require_permission
from app.core.security import Principal
from app.db.session import get_db
from app.domain.cash import CashierSession

router = APIRouter(prefix="/cash", tags=["cash"])


class OpenSessionRequest(BaseModel):
    register_id: int
    opening_cash_minor: int


class SessionOut(BaseModel):
    id: int
    register_id: int
    opening_cash_minor: int
    is_open: bool

    class Config:
        from_attributes = True


@router.get("/session/current", response_model=SessionOut | None)
def get_current_session(
    register_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("cash.manage_session")),
):
    """
    Added during the Phase 8 rebuild: there was previously no way for the
    UI to discover "is there already an open session on this register" —
    the desktop app's checkout flow worked in earlier manual testing only
    because a session had been opened once via a direct curl call against
    a persistent dev database and simply never closed. That is exactly the
    kind of undocumented, non-reproducible precondition the CTO audit
    warned about; this endpoint plus the real "Open Register" UI
    (PosPage.tsx) replace it with something a fresh checkout can actually
    discover and drive itself.
    """
    session = (
        db.query(CashierSession)
        .filter(
            CashierSession.register_id == register_id,
            CashierSession.tenant_id == principal.tenant_id,
            CashierSession.is_open.is_(True),
        )
        .order_by(CashierSession.id.desc())
        .first()
    )
    return session


@router.post("/session/open", response_model=SessionOut)
def open_session(
    body: OpenSessionRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("cash.manage_session")),
):
    existing = (
        db.query(CashierSession)
        .filter(CashierSession.register_id == body.register_id, CashierSession.is_open.is_(True))
        .first()
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="A session is already open on this register")

    if principal.store_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="This user has no store assigned — cannot open a cashier session without a store context",
        )
    session = CashierSession(
        tenant_id=principal.tenant_id,
        store_id=principal.store_id,
        register_id=body.register_id,
        cashier_user_id=principal.user_id,
        opening_cash_minor=body.opening_cash_minor,
        is_open=True,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


class CloseSessionRequest(BaseModel):
    closing_cash_minor: int


@router.post("/session/{session_id}/close", response_model=SessionOut)
def close_session(
    session_id: int,
    body: CloseSessionRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("cash.manage_session")),
):
    session = db.get(CashierSession, session_id)
    # Tenant check: previously absent, so any authenticated user of any
    # tenant could close another tenant's cashier session by guessing an id.
    if not session or not session.is_open or session.tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No open session with that id")

    session.closing_cash_minor = body.closing_cash_minor
    session.is_open = False
    session.closed_at = dt.datetime.utcnow()
    db.commit()
    db.refresh(session)
    return session
