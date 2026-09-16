"""
RBAC + a minimal ABAC hook (Phase 4).

Legacy finding: every route in the old backend only checked "is there any
valid token", never "is this role/permission allowed to do this". This
module is what a route depends on instead.

Roles are seed data (see app/domain/authz.py), not hardcoded here — the
plan requires roles to be configurable. This module only enforces
permission membership and exposes an ABAC-style `context` object routes
can use for amount-based / time-based / store-based rules (e.g. "cashier
can refund up to €20", plan §52) without hardcoding thresholds into the UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, Header, HTTPException, status

from app.core.security import Principal, decode_token, InvalidTokenError


def get_current_principal(authorization: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        return decode_token(token, expected_type="access")
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {exc}") from exc


@dataclass(frozen=True, slots=True)
class AbacContext:
    principal: Principal
    amount_minor: int | None = None
    store_id: int | None = None


def require_permission(permission: str) -> Callable[..., Principal]:
    """
    FastAPI dependency factory: `Depends(require_permission("orders.refund"))`.

    Permission -> role membership is resolved against the DB-backed
    RolePermission table (app/domain/authz.py), not a hardcoded map, so
    admins can change it without a code deploy.
    """

    def _dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        from app.db.session import SessionLocal
        from app.domain.authz import Role, RolePermission, Permission

        with SessionLocal() as db:
            has_perm = (
                db.query(RolePermission)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .join(Role, Role.id == RolePermission.role_id)
                .filter(Role.name == principal.role, Permission.code == permission)
                .first()
            )
            if not has_perm:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    detail=f"Role '{principal.role}' lacks permission '{permission}'",
                )
        return principal

    return _dependency
