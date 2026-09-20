"""
Bootstrap seed data — deliberately data, not hardcoded route logic, so an
admin can change roles/permissions/categories without a redeploy (plan
§51: "Roles must be configurable").
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.authz import Permission, Role, RolePermission
from app.domain.catalog import Category
from app.domain.tenancy import Tenant, Store, Register

DEFAULT_PERMISSIONS = [
    "orders.create",
    "orders.refund",
    "orders.refund.override",
    "products.create",
    "products.update",
    "products.delete",
    "website_orders.view",
    "website_orders.change_status",
    "sync.manage",
    "reports.view",
    "admin.manage_users",
    "cash.manage_session",
]

# Role -> permission codes. Owner/Administrator get everything; others are
# scoped per plan §51's role list.
DEFAULT_ROLE_PERMISSIONS = {
    "Owner": DEFAULT_PERMISSIONS,
    "Administrator": DEFAULT_PERMISSIONS,
    "Store Manager": [
        "orders.create",
        "orders.refund",
        "orders.refund.override",
        "products.create",
        "products.update",
        "website_orders.view",
        "website_orders.change_status",
        "sync.manage",
        "reports.view",
        "cash.manage_session",
    ],
    "Cashier": ["orders.create", "website_orders.view", "cash.manage_session"],
    "Inventory Manager": ["products.create", "products.update", "reports.view"],
    "Website Manager": ["website_orders.view", "website_orders.change_status", "sync.manage"],
}


def seed_tenant_and_store(db: Session) -> tuple[Tenant, Store]:
    tenant = db.query(Tenant).filter(Tenant.name == "India Gate").first()
    if not tenant:
        tenant = Tenant(name="India Gate")
        db.add(tenant)
        db.flush()
    store = db.query(Store).filter(Store.tenant_id == tenant.id).first()
    if not store:
        store = Store(tenant_id=tenant.id, name="India Gate — Main Store")
        db.add(store)
        db.flush()
    register = db.query(Register).filter(Register.store_id == store.id).first()
    if not register:
        register = Register(store_id=store.id, name="Register 1")
        db.add(register)
        db.flush()
    return tenant, store


def seed_rbac(db: Session) -> None:
    perm_by_code: dict[str, Permission] = {}
    for code in DEFAULT_PERMISSIONS:
        perm = db.query(Permission).filter(Permission.code == code).first()
        if not perm:
            perm = Permission(code=code)
            db.add(perm)
            db.flush()
        perm_by_code[code] = perm

    tenant, _ = seed_tenant_and_store(db)

    for role_name, codes in DEFAULT_ROLE_PERMISSIONS.items():
        role = db.query(Role).filter(Role.name == role_name, Role.tenant_id == tenant.id).first()
        if not role:
            role = Role(tenant_id=tenant.id, name=role_name)
            db.add(role)
            db.flush()
        existing = {rp.permission_id for rp in db.query(RolePermission).filter(RolePermission.role_id == role.id)}
        for code in codes:
            perm = perm_by_code[code]
            if perm.id not in existing:
                db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    db.commit()


# Seed categories known from the Phase 0 audit of the live POS (57 real
# category rows exist there — this is NOT that full messy list, just the
# ones needed to prove the Extra-exclusion rule end-to-end; Phase 20
# migration is what brings over the real 57, deduplicated).
SEED_CATEGORIES = [
    ("Grocery", True),
    ("Beverages", True),
    ("Snacks", True),
    ("Extra", False),  # <- the mandatory exclusion (plan §37)
]


def seed_categories(db: Session) -> None:
    tenant, _ = seed_tenant_and_store(db)
    for name, sync_to_website in SEED_CATEGORIES:
        slug = name.strip().lower()
        existing = db.query(Category).filter(Category.tenant_id == tenant.id, Category.slug == slug).first()
        if not existing:
            db.add(Category(tenant_id=tenant.id, name=name, slug=slug, sync_to_website=sync_to_website))
    db.commit()


def seed_all(db: Session) -> Tenant:
    tenant, _ = seed_tenant_and_store(db)
    seed_rbac(db)
    seed_categories(db)
    return tenant
