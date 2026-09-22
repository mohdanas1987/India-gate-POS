"""
Phase 7 — Order/Payment Domain.

Design decision (plan §45): a single Order model with `sales_channel`
rather than a parallel WebsiteOrder-only universe for financials —
reporting (Phase 14/62) needs POS vs website comparisons to be a WHERE
clause, not a UNION across two unrelated tables. The plan's WebsiteOrder /
WebsiteOrderLine entities (Phase 11) still exist, but as the
website-specific lifecycle/detail wrapper around one of these Orders
(external fields + status workflow), not as a competing sales record.
"""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import String, ForeignKey, Integer, DateTime, Enum, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SalesChannel(str, enum.Enum):
    POS = "POS"
    WEBSITE = "WEBSITE"
    PHONE = "PHONE"
    MANUAL = "MANUAL"


class OrderStatus(str, enum.Enum):
    OPEN = "OPEN"
    COMPLETED = "COMPLETED"
    VOIDED = "VOIDED"
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    register_id: Mapped[int | None] = mapped_column(ForeignKey("registers.id"), nullable=True)
    cashier_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Phase 9B: optional customer attachment (the `customers` table itself
    # has existed since before this phase with zero live rows — see
    # app/domain/customer.py's docstring — so this is the first thing that
    # actually links a sale to one).
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)

    sales_channel: Mapped[SalesChannel] = mapped_column(Enum(SalesChannel), default=SalesChannel.POS)
    external_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # website's own order number

    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.OPEN)

    # Phase 22 (LedgerBrug integration readiness, dev-team question #3): a
    # permanent, human/barcode-scannable receipt number, distinct from the
    # internal `id`. Derived from (store, register, id) rather than a
    # separately-tracked counter — deriving it from the already-atomic
    # primary key means it inherits "permanent, unique, never reused" for
    # free, with no separate race-prone sequence to get wrong. Nullable
    # only because it is assigned by the service after the row (and its
    # id) exist; every order created through create_pos_sale() has one.
    receipt_number: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True)

    # Phase 22, dev-team question #4 (cancellation/void): OrderStatus.VOIDED
    # existed as an enum value with nothing behind it before this. A void
    # keeps its original receipt_number (per the dev team's explicit ask)
    # and records who/why/when, same audit shape as a refund.
    voided_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    voided_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    voided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    subtotal_minor: Mapped[int] = mapped_column(Integer, default=0)
    tax_minor: Mapped[int] = mapped_column(Integer, default=0)
    discount_minor: Mapped[int] = mapped_column(Integer, default=0)
    total_minor: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )

    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order")
    payments: Mapped[list["OrderPayment"]] = relationship(back_populates="order")

    __table_args__ = ()


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))

    quantity: Mapped[int] = mapped_column(Integer)  # for weighted items, quantity is grams (integer) not kg-float
    unit_price_minor: Mapped[int] = mapped_column(Integer)
    tax_minor: Mapped[int] = mapped_column(Integer, default=0)
    # Phase 22 (LedgerBrug dev-team question #1 + the Z-report's "totals
    # per VAT rate"): the tax AMOUNT was already stored per line, but not
    # the RATE that produced it. Without this, a per-VAT-rate breakdown
    # can only be reconstructed by re-joining to the product's CURRENT tax
    # rate, which is wrong the moment a product's rate changes after the
    # sale — the same historical-snapshot principle already applied to
    # tax_minor itself (see checkout.py's module docstring). Nullable only
    # for rows written before this column existed; every new line sets it.
    tax_rate_basis_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_minor: Mapped[int] = mapped_column(Integer, default=0)
    line_total_minor: Mapped[int] = mapped_column(Integer)

    order: Mapped["Order"] = relationship(back_populates="lines")


class PaymentMethod(str, enum.Enum):
    CASH = "CASH"
    CARD = "CARD"
    GIFT_CARD = "GIFT_CARD"
    STORE_CREDIT = "STORE_CREDIT"
    EXTERNAL = "EXTERNAL"  # future real payment gateway, plugged in behind PaymentProvider (Phase 10/21)


class OrderPayment(Base):
    __tablename__ = "order_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod))
    amount_minor: Mapped[int] = mapped_column(Integer)
    provider_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    order: Mapped["Order"] = relationship(back_populates="payments")


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    requested_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("approvals.id"), nullable=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)


class RefundLine(Base):
    __tablename__ = "refund_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    refund_id: Mapped[int] = mapped_column(ForeignKey("refunds.id"))
    order_line_id: Mapped[int] = mapped_column(ForeignKey("order_lines.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    amount_minor: Mapped[int] = mapped_column(Integer)
