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

    sales_channel: Mapped[SalesChannel] = mapped_column(Enum(SalesChannel), default=SalesChannel.POS)
    external_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # website's own order number

    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.OPEN)

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
