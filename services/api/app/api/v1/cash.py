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
from app.services.authorization import AuthorizationError, resolve_authorized_register
from app.services.cash_sessions import CashSessionAlreadyOpenError, CashSessionNotPermittedError, open_cashier_session

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
    # Register-ownership check (CTO audit of 0cfd8ca, finding #7): only
    # enforced when this principal is scoped to a single store — a
    # multi-store role (principal.store_id is None) is allowed to check
    # any register's status within its own tenant, same as the existing
    # tenant-only filter below.
    if principal.store_id is not None:
        try:
            resolve_authorized_register(db, principal.tenant_id, principal.store_id, register_id)
        except AuthorizationError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

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
    if principal.store_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="This user has no store assigned — cannot open a cashier session without a store context",
        )

    # Phase 22.1: pulled into a shared service (app/services/cash_sessions.py)
    # so the offline sync path (app/api/v1/sync.py's "cash_session.open"
    # handling) enforces exactly the same register-ownership check and
    # independent cash.manage_session permission check as this route does
    # — "centralize resolve_authorized_register() and use it everywhere"
    # applied to session-open itself, not just to the check inside it.
    try:
        session = open_cashier_session(
            db,
            tenant_id=principal.tenant_id,
            store_id=principal.store_id,
            register_id=body.register_id,
            principal=principal,
            opening_cash_minor=body.opening_cash_minor,
        )
    except CashSessionAlreadyOpenError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CashSessionNotPermittedError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
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
