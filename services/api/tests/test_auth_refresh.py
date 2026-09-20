"""
Phase 8.5 (CTO audit of 0cfd8ca, finding #4): login() has always issued a
refresh_token, but nothing could ever redeem it. This tests the new
POST /api/v1/auth/refresh endpoint by calling the route function directly,
matching this suite's established convention (see test_sync.py's note on
why TestClient can't be used here).
"""
from fastapi import HTTPException

from app.api.v1.auth import RefreshRequest, refresh
from app.core.security import create_token, decode_token
from app.domain.authz import Role, User, UserRole
from app.domain.seed import seed_all
from app.domain.tenancy import Store


def _make_user_with_role(db, tenant, role_name="Cashier"):
    store = db.query(Store).filter(Store.tenant_id == tenant.id).first()
    user = User(tenant_id=tenant.id, name="Refresh Test User", email="refresh-test@test-fixture.local", password_hash="x", is_active=True)
    db.add(user)
    db.flush()
    role = db.query(Role).filter(Role.tenant_id == tenant.id, Role.name == role_name).first()
    db.add(UserRole(user_id=user.id, role_id=role.id, store_id=store.id))
    db.commit()
    db.refresh(user)
    return user, store


def test_refresh_token_issues_a_new_valid_access_token(db):
    tenant = seed_all(db)
    user, store = _make_user_with_role(db, tenant)
    refresh_token = create_token(user.id, tenant.id, "Cashier", store.id, "refresh")

    response = refresh(RefreshRequest(refresh_token=refresh_token), db)

    principal = decode_token(response.access_token, expected_type="access")
    assert principal.user_id == user.id
    assert principal.tenant_id == tenant.id
    assert principal.role == "Cashier"


def test_refresh_rejects_an_access_token_used_as_a_refresh_token(db):
    tenant = seed_all(db)
    user, store = _make_user_with_role(db, tenant)
    access_token = create_token(user.id, tenant.id, "Cashier", store.id, "access")

    try:
        refresh(RefreshRequest(refresh_token=access_token), db)
        assert False, "expected HTTPException for wrong token type"
    except HTTPException as exc:
        assert exc.status_code == 401


def test_refresh_rejects_a_deactivated_user(db):
    tenant = seed_all(db)
    user, store = _make_user_with_role(db, tenant)
    refresh_token = create_token(user.id, tenant.id, "Cashier", store.id, "refresh")

    user.is_active = False
    db.commit()

    try:
        refresh(RefreshRequest(refresh_token=refresh_token), db)
        assert False, "expected HTTPException for deactivated user"
    except HTTPException as exc:
        assert exc.status_code == 401


def test_refresh_reflects_a_role_change_since_the_token_was_issued(db):
    """The refresh token still says 'Cashier', but the user's actual role
    membership changed to 'Store Manager' in the meantime — the new
    access token must reflect the CURRENT role, not the stale claim."""
    tenant = seed_all(db)
    user, store = _make_user_with_role(db, tenant, role_name="Cashier")
    refresh_token = create_token(user.id, tenant.id, "Cashier", store.id, "refresh")

    # Promote the user: remove the Cashier role membership, add Store Manager.
    db.query(UserRole).filter(UserRole.user_id == user.id).delete()
    manager_role = db.query(Role).filter(Role.tenant_id == tenant.id, Role.name == "Store Manager").first()
    db.add(UserRole(user_id=user.id, role_id=manager_role.id, store_id=store.id))
    db.commit()
    db.refresh(user)

    response = refresh(RefreshRequest(refresh_token=refresh_token), db)
    principal = decode_token(response.access_token, expected_type="access")
    assert principal.role == "Store Manager"
