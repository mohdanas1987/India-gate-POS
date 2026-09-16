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
from app.core.security import Principal, create_token, hash_password, verify_password
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

    # role/store resolution kept simple here; a user with multiple
    # store-scoped roles selects a store client-side after login in the
    # full implementation (Phase 4 UI concern, not this endpoint's job).
    role_name = user.roles[0].role.name if user.roles else "cashier"
    store_id = user.roles[0].store_id if user.roles else None

    return TokenResponse(
        access_token=create_token(user.id, role_name, store_id, "access"),
        refresh_token=create_token(user.id, role_name, store_id, "refresh"),
    )


class MeResponse(BaseModel):
    user_id: int
    role: str
    store_id: int | None


@router.get("/me", response_model=MeResponse)
def me(principal: Principal = Depends(get_current_principal)):
    return MeResponse(user_id=principal.user_id, role=principal.role, store_id=principal.store_id)
