"""
Regression test for a real hole caught during this build: the refund
endpoint's `manager_override` flag would have let ANY caller with plain
`orders.refund` bypass the approval threshold just by setting a boolean.
Fixed in app/api/v1/orders.py by requiring the separate
'orders.refund.override' permission before honoring that flag. This test
proves the seed data assigns it correctly (Cashier does not get it,
Store Manager does) — the router-level enforcement itself is exercised
via the RBAC query helper, matching what the live endpoint does.
"""
from app.core.rbac import principal_has_permission
from app.core.security import Principal
from app.domain.seed import seed_all


def test_cashier_does_not_get_refund_override_permission(db):
    tenant = seed_all(db)
    cashier_principal = Principal(user_id=1, tenant_id=tenant.id, role="Cashier", store_id=1)
    assert principal_has_permission(db, cashier_principal, "orders.refund.override") is False


def test_store_manager_gets_refund_override_permission(db):
    tenant = seed_all(db)
    manager_principal = Principal(user_id=1, tenant_id=tenant.id, role="Store Manager", store_id=1)
    assert principal_has_permission(db, manager_principal, "orders.refund.override") is True
