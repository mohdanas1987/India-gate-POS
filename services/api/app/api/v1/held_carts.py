"""
Phase 9B — suspended/held carts (HOLD/RECALL). See app/domain/held_cart.py
for why this is a snapshot, not a live draft-order mechanism.

Recall CONSUMES the row (deletes it) — the CTO plan's own model is
HOLD -> RECALL as a one-shot pair, not a saved-carts library a cashier
keeps auditing. This also sidesteps a class of bugs where the SAME held
cart gets recalled twice on two terminals and rung up twice.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.rbac import require_permission
from app.core.security import Principal
from app.db.session import get_db
from app.domain.held_cart import HeldCart
from app.services.authorization import AuthorizationError, resolve_authorized_register

router = APIRouter(prefix="/carts", tags=["carts"])


class HeldCartLineIn(BaseModel):
    product_id: int
    quantity: int
    discount_minor: int = 0


class HoldCartRequest(BaseModel):
    register_id: int
    customer_id: int | None = None
    label: str | None = None
    lines: list[HeldCartLineIn]


class HeldCartOut(BaseModel):
    id: int
    register_id: int
    customer_id: int | None
    label: str | None
    lines: list[HeldCartLineIn]
    held_at: str

    @classmethod
    def from_row(cls, row: HeldCart) -> "HeldCartOut":
        return cls(
            id=row.id, register_id=row.register_id, customer_id=row.customer_id, label=row.label,
            lines=[HeldCartLineIn(**line) for line in row.lines_json],
            held_at=row.held_at.isoformat(),
        )


@router.post("/hold", response_model=HeldCartOut, status_code=status.HTTP_201_CREATED)
def hold_cart(
    body: HoldCartRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("orders.create")),
):
    if principal.store_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This user has no store assigned — cannot hold a cart")
    if not body.lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot hold an empty cart")
    try:
        resolve_authorized_register(db, principal.tenant_id, principal.store_id, body.register_id)
    except AuthorizationError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    row = HeldCart(
        tenant_id=principal.tenant_id,
        store_id=principal.store_id,
        register_id=body.register_id,
        cashier_user_id=principal.user_id,
        customer_id=body.customer_id,
        label=body.label,
        lines_json=[l.model_dump() for l in body.lines],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return HeldCartOut.from_row(row)


@router.get("/held", response_model=list[HeldCartOut])
def list_held_carts(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("orders.create")),
):
    query = db.query(HeldCart).filter(HeldCart.tenant_id == principal.tenant_id)
    # Store-scoped like every other resource in this file's family
    # (website orders, cash sessions) — a store-scoped cashier only sees
    # THIS store's held carts, not the whole tenant's.
    if principal.store_id is not None:
        query = query.filter(HeldCart.store_id == principal.store_id)
    rows = query.order_by(HeldCart.held_at.desc()).all()
    return [HeldCartOut.from_row(r) for r in rows]


@router.post("/held/{held_cart_id}/recall", response_model=HeldCartOut)
def recall_held_cart(
    held_cart_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("orders.create")),
):
    row = db.get(HeldCart, held_cart_id)
    if not row or row.tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Held cart not found")
    if principal.store_id is not None and row.store_id != principal.store_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Held cart not found")

    out = HeldCartOut.from_row(row)
    db.delete(row)  # recall consumes it — see module docstring
    db.commit()
    return out
