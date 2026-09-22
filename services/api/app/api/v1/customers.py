"""
Phase 9B — customer selection at the POS. The `customers` table has
existed since before this phase (see app/domain/customer.py's docstring:
"legacy table exists but has 0 live rows"), so this is the first API
surface that actually reads/writes it. Loyalty/gift-card/store-credit are
explicitly deferred to Phase 14 (Admin Platform), same as that module's
own docstring already states — this stays a plain name/phone/email/
address record, enough to attach a customer to a sale and look them up
again next time.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.rbac import require_permission
from app.core.security import Principal
from app.db.session import get_db
from app.domain.customer import Customer

router = APIRouter(prefix="/customers", tags=["customers"])


class CustomerOut(BaseModel):
    id: int
    name: str
    phone: str | None
    email: str | None
    street: str | None
    city: str | None

    class Config:
        from_attributes = True


@router.get("", response_model=list[CustomerOut])
def search_customers(
    q: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("customers.manage")),
):
    query = select(Customer).where(Customer.tenant_id == principal.tenant_id)
    if q:
        like = f"%{q}%"
        query = query.where(or_(Customer.name.ilike(like), Customer.phone.ilike(like), Customer.email.ilike(like)))
    return db.execute(query.order_by(Customer.name).limit(min(limit, 200))).scalars().all()


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("customers.manage")),
):
    customer = db.get(Customer, customer_id)
    if not customer or customer.tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Customer not found")
    return customer


class CustomerCreate(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None
    street: str | None = None
    city: str | None = None


@router.post("", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(
    body: CustomerCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("customers.manage")),
):
    customer = Customer(
        tenant_id=principal.tenant_id, name=body.name, phone=body.phone, email=body.email,
        street=body.street, city=body.city,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer
