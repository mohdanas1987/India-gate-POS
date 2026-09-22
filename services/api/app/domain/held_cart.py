"""
Phase 9B — suspended/held carts (the CTO plan's HOLD/RECALL feature).

Deliberately NOT a second, competing cart-persistence mechanism: the
Phase 9A gate explicitly deferred general cart persistence to this
feature rather than half-building it twice (see PHASE-STATUS.md's Phase
9A "Known limitations"). A held cart is a simple snapshot — enough to
rebuild the cart UI exactly as it was — not a live, continuously-synced
draft order. It is deleted the moment it's recalled (recall consumes it),
so there is exactly one authoritative copy of any given suspended sale at
a time.

`lines_json` stores a plain list of dicts, not a normalized child table:
[{"product_id": int, "quantity": int, "discount_minor": int}, ...]. A
held cart is short-lived scratch state (a cashier stepping away for a
customer's forgotten wallet), not a durable financial record — an
OrderLine only gets created once the sale actually completes.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import ForeignKey, String, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HeldCart(Base):
    __tablename__ = "held_carts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    register_id: Mapped[int] = mapped_column(ForeignKey("registers.id"))
    cashier_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lines_json: Mapped[list] = mapped_column(JSON)
    held_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
