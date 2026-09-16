"""
Phase 6 — Inventory Ledger.

Legacy finding: `products.quantity` is the ONLY stock signal in the old
schema — a single mutable number with no history, so "why is stock wrong"
is unanswerable today. This module replaces that with an append-only
ledger; current stock is a derived projection (SUM of ledger deltas),
matching plan §14's reconstructable-inventory requirement exactly:

    Opening Stock + Purchases + Transfers In + Returns
    - Sales - Transfers Out - Damages ± Adjustments = Current Stock
"""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import String, ForeignKey, Integer, DateTime, Enum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LedgerEventType(str, enum.Enum):
    OPENING_BALANCE = "OPENING_BALANCE"
    SALE = "SALE"
    PURCHASE_RECEIPT = "PURCHASE_RECEIPT"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    RETURN = "RETURN"
    DAMAGE = "DAMAGE"
    ADJUSTMENT = "ADJUSTMENT"
    STOCK_COUNT = "STOCK_COUNT"
    WEBSITE_RESERVATION = "WEBSITE_RESERVATION"  # reserved but not yet decremented — see reservations note below
    WEBSITE_RESERVATION_RELEASE = "WEBSITE_RESERVATION_RELEASE"


class InventoryLedger(Base):
    __tablename__ = "inventory_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))

    event_type: Mapped[LedgerEventType] = mapped_column(Enum(LedgerEventType))
    quantity_delta: Mapped[int] = mapped_column(Integer)  # signed; negative for sales/transfers-out/damage
    reference_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. "order", "purchase_order"
    reference_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)


class InventoryReservationPolicy(str, enum.Enum):
    """When does a website order actually decrement stock? Plan §46
    explicitly says this must be configurable, not hardcoded."""

    ON_ORDER_CREATION = "ON_ORDER_CREATION"
    ON_CONFIRMATION = "ON_CONFIRMATION"
    ON_PACKING = "ON_PACKING"
    ON_FULFILLMENT = "ON_FULFILLMENT"
