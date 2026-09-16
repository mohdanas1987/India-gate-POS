"""
Cash register / cashier session domain.

Legacy finding: `cashier_sessions` has exactly 66,191 rows against 66,191
orders — a 1:1 ratio, meaning the legacy schema writes one "session" row
per ORDER, not per shift. That's a naming collision with the plan's
CashierSession concept (one row per cashier shift, containing many
orders). This new schema uses the plan's intended meaning; Phase 20
migration must re-derive real shift boundaries from the legacy
`cash_register` table (556 rows — much closer to a plausible shift count)
rather than copying `cashier_sessions` 1:1.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import String, ForeignKey, Integer, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CashierSession(Base):
    """One row per shift: open register -> (many orders) -> close register."""

    __tablename__ = "cashier_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    register_id: Mapped[int] = mapped_column(ForeignKey("registers.id"))
    cashier_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    opening_cash_minor: Mapped[int] = mapped_column(Integer)
    closing_cash_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_cash_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    variance_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)

    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)


class CashMovement(Base):
    """Manual cash in/out during a session (float top-up, safe drop, paid-out)."""

    __tablename__ = "cash_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("cashier_sessions.id"))
    amount_minor: Mapped[int] = mapped_column(Integer)  # signed
    reason: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)


class DayClose(Base):
    __tablename__ = "day_closes"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    business_date: Mapped[dt.date] = mapped_column()
    total_sales_minor: Mapped[int] = mapped_column(Integer)
    total_tax_minor: Mapped[int] = mapped_column(Integer)
    total_refunds_minor: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
