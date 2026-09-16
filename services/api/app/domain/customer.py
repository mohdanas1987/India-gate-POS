"""Minimal Customer entity — legacy table exists but has 0 live rows, so
there's no migration-reconciliation risk here yet. Loyalty/GiftCard/
StoreCredit (plan §12) are intentionally deferred to Phase 14 (Admin
Platform) since they have zero production usage today and would only add
untested surface area to earlier phases."""
from __future__ import annotations

from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    street: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
