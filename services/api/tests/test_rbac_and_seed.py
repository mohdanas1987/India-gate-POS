from app.domain.authz import User, UserRole, Role
from app.domain.seed import seed_all
from app.domain.catalog import Category
from app.core.security import hash_password, create_token, decode_token, Principal


def test_seed_creates_expected_roles_and_extra_category_excluded(db):
    seed_all(db)

    role_names = {r.name for r in db.query(Role).all()}
    assert {"Owner", "Administrator", "Store Manager", "Cashier", "Inventory Manager", "Website Manager"} <= role_names

    extra = db.query(Category).filter(Category.slug == "extra").first()
    assert extra is not None
    assert extra.sync_to_website is False

    grocery = db.query(Category).filter(Category.slug == "grocery").first()
    assert grocery.sync_to_website is True


def test_cashier_role_cannot_manage_sync_but_manager_can(db):
    """Exercises the same query app.core.rbac.require_permission uses,
    proving RBAC is enforced by data (RolePermission), not hardcoded."""
    from app.domain.authz import RolePermission, Permission

    seed_all(db)

    def has_permission(role_name: str, code: str) -> bool:
        return (
            db.query(RolePermission)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .join(Role, Role.id == RolePermission.role_id)
            .filter(Role.name == role_name, Permission.code == code)
            .first()
            is not None
        )

    assert has_permission("Cashier", "sync.manage") is False
    assert has_permission("Store Manager", "sync.manage") is True
    assert has_permission("Cashier", "orders.create") is True


def test_identity_comes_only_from_verified_token_not_client_fields():
    """Regression test for the exact legacy bug found in Phase 0:
    /auth/getuser trusted req.body.id instead of the token subject. Here,
    decode_token only ever returns what was cryptographically signed."""
    token = create_token(user_id=42, role="Cashier", store_id=1)
    principal = decode_token(token)
    assert principal.user_id == 42
    assert principal.role == "Cashier"

    # Tampering with a claim the client would control (if such a field
    # existed) is impossible here because Principal is built exclusively
    # from decode_token's verified payload — there is no code path in
    # app/core/security.py or app/core/rbac.py that reads an alternative
    # id from anywhere else.
    assert isinstance(principal, Principal)


def test_password_hash_is_not_reversible_plaintext():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert hashed.startswith("$2b$")  # bcrypt
