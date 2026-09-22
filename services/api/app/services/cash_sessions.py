"""
Phase 22.1 — centralized cashier-session-open logic, shared by the direct
online endpoint (app/api/v1/cash.py) and the offline sync path
(app/api/v1/sync.py's new "cash_session.open" handling). Pulled out of
cash.py rather than duplicated, per the CTO's repeated "centralize and use
it everywhere" theme (already applied to resolve_authorized_register()
this way in Phase 8.5).

Independent domain-layer permission check (same discipline as
process_refund/void_order): a critical operational action should not
depend entirely on the route/permission-dependency remembering to gate
it, especially now that a SECOND caller (the sync endpoint) reaches this
function through a different route dependency (orders.create, not
cash.manage_session — see sync.py's own comment on why).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.rbac import principal_has_permission
from app.core.security import Principal
from app.domain.cash import CashierSession
from app.services.authorization import AuthorizationError, resolve_authorized_register


class CashSessionError(Exception):
    pass


class CashSessionNotPermittedError(CashSessionError):
    pass


class CashSessionAlreadyOpenError(CashSessionError):
    def __init__(self, existing: CashierSession):
        self.existing = existing
        super().__init__(f"A session is already open on register {existing.register_id}")


def open_cashier_session(
    db: Session,
    tenant_id: int,
    store_id: int,
    register_id: int,
    principal: Principal,
    opening_cash_minor: int,
) -> CashierSession:
    if not principal_has_permission(db, principal, "cash.manage_session"):
        raise CashSessionNotPermittedError("Caller does not hold cash.manage_session")

    try:
        resolve_authorized_register(db, tenant_id, store_id, register_id)
    except AuthorizationError as exc:
        raise CashSessionNotPermittedError(str(exc)) from exc

    existing = (
        db.query(CashierSession)
        .filter(CashierSession.register_id == register_id, CashierSession.is_open.is_(True))
        .first()
    )
    if existing:
        raise CashSessionAlreadyOpenError(existing)

    session = CashierSession(
        tenant_id=tenant_id,
        store_id=store_id,
        register_id=register_id,
        cashier_user_id=principal.user_id,
        opening_cash_minor=opening_cash_minor,
        is_open=True,
    )
    db.add(session)
    db.flush()
    return session
