"""
Phase 8.5 — Security + Transaction Integrity Gate.

CTO audit of commit 0cfd8ca, findings #6 and #7: checkout and cash-session
open both trusted an id the caller supplied (register_id) without proving
it actually belongs to the caller's own tenant/store. The route layer's
`principal.tenant_id`/`principal.store_id` checks are necessary but not
sufficient — a domain service must never assume "if the caller says
register_id=7, register 7 belongs to them." It must prove it.

This module is the single place that proves it, so every service that
needs a register/store/tenant chain resolved and validated calls the same
function instead of re-implementing (and potentially re-breaking) the
check inline. The CTO's own words: "Centralize resolve_authorized_register()
and use it everywhere."
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.catalog import Product
from app.domain.customer import Customer
from app.domain.tenancy import Register, Store


class AuthorizationError(Exception):
    """Raised when a resource id the caller supplied does not actually
    belong to the tenant/store the caller is authenticated as. Deliberately
    a distinct exception type from CheckoutError/RefundError/etc. so a
    route can choose to map it to 403/404 rather than 409/422 — this is an
    authorization failure, not a business-rule validation failure."""


def resolve_authorized_register(db: Session, tenant_id: int, store_id: int, register_id: int) -> Register:
    """
    Resolves register_id -> Register -> Store -> Tenant and validates the
    full chain against the caller's own tenant_id/store_id, exactly as the
    CTO audit specified:

        register.store_id == principal.store_id
        store.tenant_id == principal.tenant_id

    Raises AuthorizationError (never returns a register that fails either
    check) so callers cannot accidentally proceed with an unvalidated id.
    """
    register = db.get(Register, register_id)
    if register is None:
        raise AuthorizationError(f"Register {register_id} does not exist")

    store = db.get(Store, register.store_id)
    if store is None or store.tenant_id != tenant_id:
        raise AuthorizationError(
            f"Register {register_id} does not belong to tenant {tenant_id}"
        )

    if register.store_id != store_id:
        raise AuthorizationError(
            f"Register {register_id} belongs to store {register.store_id}, not store {store_id}"
        )

    return register


def resolve_authorized_product(db: Session, tenant_id: int, product_id: int) -> Product:
    """
    Phase 9B correction gate (CTO review of 96f6aa9, security issue #1):
    the held-cart HOLD endpoint accepted a bare product_id inside its JSON
    lines payload and stored it without ever checking it belongs to the
    caller's own tenant — a cross-tenant (or deleted/soft-deleted) product
    id could sit inside persistent operational state even though the
    eventual checkout would still independently reject it. Every place
    that accepts a product_id from an untrusted request body and is about
    to PERSIST it (not just read it back through an already-tenant-scoped
    query) must resolve it through here first, same discipline as
    resolve_authorized_register above.
    """
    product = db.get(Product, product_id)
    if product is None or product.tenant_id != tenant_id or product.is_deleted:
        raise AuthorizationError(f"Product {product_id} does not exist for this tenant")
    if not product.pos_visible:
        raise AuthorizationError(f"Product {product_id} is not visible at the POS")
    return product


def resolve_authorized_customer(db: Session, tenant_id: int, customer_id: int) -> Customer:
    """Same reasoning as resolve_authorized_product, for a customer_id
    accepted from an untrusted request body before it's persisted."""
    customer = db.get(Customer, customer_id)
    if customer is None or customer.tenant_id != tenant_id:
        raise AuthorizationError(f"Customer {customer_id} does not exist for this tenant")
    return customer
