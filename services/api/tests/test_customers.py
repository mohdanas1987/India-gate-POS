"""Phase 9B — customer search/create at the POS."""
from __future__ import annotations

import pytest

from app.api.v1.customers import CustomerCreate, create_customer, get_customer, search_customers
from app.core.security import Principal
from app.domain.authz import User
from app.domain.customer import Customer
from app.domain.seed import seed_all
from fastapi import HTTPException


@pytest.fixture
def customer_setup(db):
    tenant = seed_all(db)
    user = User(tenant_id=tenant.id, name="Cust Cashier", email="cust-cashier@test-fixture.local", password_hash="x")
    db.add(user)
    db.commit()
    principal = Principal(user_id=user.id, tenant_id=tenant.id, role="Cashier", store_id=None)
    return {"principal": principal, "tenant_id": tenant.id}


def test_create_and_search_customer_by_name(db, customer_setup):
    created = create_customer(
        CustomerCreate(name="Jane Doe", phone="0612345678", email="jane@example.com"),
        db=db, principal=customer_setup["principal"],
    )
    assert created.id is not None

    results = search_customers(q="Jane", limit=20, db=db, principal=customer_setup["principal"])
    assert [c.id for c in results] == [created.id]


def test_search_matches_phone_too(db, customer_setup):
    created = create_customer(
        CustomerCreate(name="John Smith", phone="0698765432"),
        db=db, principal=customer_setup["principal"],
    )
    results = search_customers(q="0698765432", limit=20, db=db, principal=customer_setup["principal"])
    assert [c.id for c in results] == [created.id]


def test_customer_search_is_tenant_scoped(db, customer_setup):
    from app.domain.tenancy import Tenant

    other_tenant = Tenant(name="Other Tenant")
    db.add(other_tenant)
    db.flush()
    db.add(Customer(tenant_id=other_tenant.id, name="Cross Tenant Customer"))
    db.commit()

    results = search_customers(q="Cross Tenant", limit=20, db=db, principal=customer_setup["principal"])
    assert results == []


def test_get_customer_not_found_returns_404(db, customer_setup):
    with pytest.raises(HTTPException) as exc_info:
        get_customer(999999, db=db, principal=customer_setup["principal"])
    assert exc_info.value.status_code == 404
