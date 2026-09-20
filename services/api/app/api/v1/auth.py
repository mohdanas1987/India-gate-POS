"""
Phase 2 replacement for legacy /auth routes.

Fixes vs legacy (Phase 0 findings):
  - No hardcoded secret (app.core.security reads it from Settings).
  - /me derives identity strictly from the verified token, never from a
    client-supplied field (the legacy `/getuser` bug).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.rbac import get_current_principal
from app.core.security import InvalidTokenError, Principal, create_token, decode_token, hash_password, verify_password
from app.db.session import get_db
from app.domain.authz import User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="User is deactivated")

    # KNOWN LIMITATION (CTO audit, commit c4bfb82, finding #11): this still
    # just takes roles[0] rather than letting a user with multiple
    # store-scoped roles (Store A -> Cashier, Store B -> Manager) choose
    # which context to operate in at login. A real fix needs a second step
    # -- list the user's (role, store) memberships and let them pick one --
    # which is a UI feature, not something this endpoint can fabricate on
    # its own. What IS fixed here: tenant_id is no longer hardcoded
    # anywhere downstream -- it comes from the authenticated user record,
    # never a literal 1.
    role_name = user.roles[0].role.name if user.roles else "cashier"
    store_id = user.roles[0].store_id if user.roles else None

    return TokenResponse(
        access_token=create_token(user.id, user.tenant_id, role_name, store_id, "access"),
        refresh_token=create_token(user.id, user.tenant_id, role_name, store_id, "refresh"),
    )


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    """
    Phase 8.5 (CTO audit of 0cfd8ca, finding #4/P1: "there is no proper
    refresh-token/offline-session lifecycle yet ... access token expiry
    while offline means synchronization resumes after online
    re-authentication"). login() has always issued a refresh_token, but
    until this endpoint existed nothing could ever redeem it — the only
    way to get a new access token was a full re-login, exactly the gap
    the audit called out.

    This does NOT just re-sign the same claims from the refresh token —
    it re-reads the user from the database and re-derives role/store_id
    fresh, the same way login() does. A refresh token issued before a
    role change, a store reassignment, or a deactivation must reflect
    that change immediately, not carry the stale claims forward for
    up to jwt_refresh_token_days.
    """
    try:
        principal = decode_token(body.refresh_token, expected_type="refresh")
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"Invalid refresh token: {exc}") from exc

    user = db.get(User, principal.user_id)
    if not user or not user.is_active or user.tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="User no longer valid for this refresh token")

    role_name = user.roles[0].role.name if user.roles else "cashier"
    store_id = user.roles[0].store_id if user.roles else None

    return TokenResponse(
        access_token=create_token(user.id, user.tenant_id, role_name, store_id, "access"),
        # Rotated, not reused: the old refresh token becomes stale-but-not-
        # revoked (there is no revocation list yet — a real gap, disclosed
        # in PHASE-STATUS.md rather than silently left unmentioned), but
        # rotation at least means a captured refresh token stops being the
        # single artifact reused indefinitely across every future refresh.
        refresh_token=create_token(user.id, user.tenant_id, role_name, store_id, "refresh"),
    )


class MeResponse(BaseModel):
    user_id: int
    tenant_id: int
    role: str
    store_id: int | None


@router.get("/me", response_model=MeResponse)
def me(principal: Principal = Depends(get_current_principal)):
    return MeResponse(
        user_id=principal.user_id, tenant_id=principal.tenant_id, role=principal.role, store_id=principal.store_id
    )
