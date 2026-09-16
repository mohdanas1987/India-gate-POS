"""
Phase 11 — Website Order lifecycle wrapper around Order (see orders.py for
why Order itself carries sales_channel/external_order_id).

Status set and transition graph are exactly as specified in the plan
(§20/§21). The transition table is DATA (WebsiteOrderStatusTransition
allowed-pairs), not an if/elif ladder in a route handler, so new statuses
or transitions (plan §20: "do not hard-code the UI so future statuses
cannot be added") can be added by an admin without a redeploy.
"""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import String, ForeignKey, DateTime, Enum, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WebsiteOrderStatus(str, enum.Enum):
    NEW = "NEW"
    CONFIRMED = "CONFIRMED"
    PROCESSING = "PROCESSING"
    PACKING = "PACKING"
    READY_FOR_DISPATCH = "READY_FOR_DISPATCH"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    ON_HOLD = "ON_HOLD"


# Default allowed transitions (plan §21). Seeded into a DB table
# (WebsiteOrderStatusTransition) at bootstrap so it can be edited without
# a code change — this constant is the seed source of truth, not the
# runtime source of truth.
DEFAULT_ALLOWED_TRANSITIONS: dict[WebsiteOrderStatus, set[WebsiteOrderStatus]] = {
    WebsiteOrderStatus.NEW: {WebsiteOrderStatus.CONFIRMED, WebsiteOrderStatus.CANCELLED},
    WebsiteOrderStatus.CONFIRMED: {WebsiteOrderStatus.PROCESSING, WebsiteOrderStatus.CANCELLED},
    WebsiteOrderStatus.PROCESSING: {WebsiteOrderStatus.PACKING, WebsiteOrderStatus.CANCELLED},
    WebsiteOrderStatus.PACKING: {WebsiteOrderStatus.READY_FOR_DISPATCH},
    WebsiteOrderStatus.READY_FOR_DISPATCH: {WebsiteOrderStatus.OUT_FOR_DELIVERY},
    WebsiteOrderStatus.OUT_FOR_DELIVERY: {WebsiteOrderStatus.DELIVERED},
    WebsiteOrderStatus.DELIVERED: {WebsiteOrderStatus.COMPLETED, WebsiteOrderStatus.REFUNDED},
    WebsiteOrderStatus.COMPLETED: {WebsiteOrderStatus.REFUNDED},
    WebsiteOrderStatus.CANCELLED: set(),
    WebsiteOrderStatus.FAILED: set(),
    WebsiteOrderStatus.REFUNDED: set(),
    WebsiteOrderStatus.ON_HOLD: {
        WebsiteOrderStatus.CONFIRMED,
        WebsiteOrderStatus.PROCESSING,
        WebsiteOrderStatus.CANCELLED,
    },
}


class WebsiteOrder(Base):
    __tablename__ = "website_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))  # the underlying financial Order

    external_order_number: Mapped[str] = mapped_column(String(100))
    status: Mapped[WebsiteOrderStatus] = mapped_column(Enum(WebsiteOrderStatus), default=WebsiteOrderStatus.NEW)

    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    customer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_method: Mapped[str] = mapped_column(String(20), default="DELIVERY")  # DELIVERY|PICKUP
    delivery_address: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    payment_status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PAID|PENDING|FAILED|REFUNDED|COD
    order_notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    pending_website_sync: Mapped[bool] = mapped_column(Boolean, default=False)  # true while a status change is queued offline

    __table_args__ = ()


class WebsiteOrderStatusHistory(Base):
    __tablename__ = "website_order_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    website_order_id: Mapped[int] = mapped_column(ForeignKey("website_orders.id"))
    old_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    new_status: Mapped[str] = mapped_column(String(30))
    changed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(20))  # WEBSITE|POS|ADMIN|SYSTEM
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)


class WebsiteSyncEvent(Base):
    """Idempotency/audit record for every inbound/outbound website event —
    prevents duplicate order ingestion and sync loops (plan §44/§41)."""

    __tablename__ = "website_sync_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    direction: Mapped[str] = mapped_column(String(10))  # IN | OUT
    event_type: Mapped[str] = mapped_column(String(50))  # order.created, order.status_changed, product.price_changed...
    external_event_id: Mapped[str] = mapped_column(String(150), unique=True)  # enforces idempotent processing
    payload: Mapped[dict] = mapped_column(JSON)
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
