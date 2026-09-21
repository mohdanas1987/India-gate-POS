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

from sqlalchemy import String, ForeignKey, Integer, DateTime, Boolean, JSON
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
    """
    Phase 22 (LedgerBrug dev-team question #5, X/Z report). This model
    existed as schema-only dead weight before this pass — nothing ever
    wrote to it. Extended rather than replaced, plus one real bug fixed
    alongside the extension: it had no `tenant_id` at all, unlike every
    other tenant-scoped table in this schema (the same class of gap the
    Phase 8.5 gate closed on orders/sessions/registers) — a store id alone
    is not a safe tenant boundary to query by.

    X vs Z is `is_finalized`, not two separate tables: an X-report is this
    same computation run on demand and returned WITHOUT being persisted
    (see app/services/reports.py); a Z-report persists exactly one
    `is_finalized=True` row per (tenant, store, business_date) and refuses
    to be run twice — finalizing twice would be exactly the kind of
    silent double-booking LedgerBrug is relying on this report to avoid.
    """

    __tablename__ = "day_closes"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    business_date: Mapped[dt.date] = mapped_column()
    is_finalized: Mapped[bool] = mapped_column(Boolean, default=False)
    closed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    total_sales_minor: Mapped[int] = mapped_column(Integer)
    total_tax_minor: Mapped[int] = mapped_column(Integer)
    total_refunds_minor: Mapped[int] = mapped_column(Integer)
    total_voided_minor: Mapped[int] = mapped_column(Integer, default=0)
    voided_count: Mapped[int] = mapped_column(Integer, default=0)
    receipt_count: Mapped[int] = mapped_column(Integer, default=0)
    first_receipt_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_receipt_number: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Per-VAT-rate and per-payment-method breakdowns, explicitly asked for
    # by LedgerBrug's question #5. Stored as JSON rather than a child
    # table: the set of rates/methods that appear on a given day is small
    # and the report is read as one whole document, never queried by an
    # individual rate/method row — a child table would add a join for
    # something that's always read and written all at once.
    # vat_breakdown: [{"tax_rate_basis_points": int, "taxable_minor": int, "tax_minor": int}, ...]
    # payment_breakdown: [{"method": str, "amount_minor": int, "count": int}, ...]
    vat_breakdown: Mapped[list | None] = mapped_column(JSON, nullable=True)
    payment_breakdown: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
