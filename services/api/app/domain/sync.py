"""
Phase 8 — Outbox / sync engine primitives (cloud side).

The POS-side outbox (what the offline Electron/SQLite client writes to
before it has connectivity) lives in the desktop app's own local schema
(apps/pos/desktop/src/sync/schema.ts) — SQLite there, not Postgres here.
This module is the CLOUD-side mirror: where inbound device events land,
keyed so re-delivery is safe (plan §16: UNIQUE(event_id) prevents
duplicate processing of a re-sent event).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import String, ForeignKey, DateTime, Integer, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InboxEvent(Base):
    """Every event a device has ever sent, deduplicated by event_id."""

    __tablename__ = "inbox_events"
    __table_args__ = (UniqueConstraint("event_id", name="uq_inbox_event_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))

    event_id: Mapped[str] = mapped_column(String(64))  # client-generated UUID — the idempotency key
    event_type: Mapped[str] = mapped_column(String(50))
    aggregate_type: Mapped[str] = mapped_column(String(50))
    aggregate_id: Mapped[str] = mapped_column(String(100))
    sequence: Mapped[int] = mapped_column(Integer)  # per-device monotonic sequence, for ordering/gap detection

    payload: Mapped[dict] = mapped_column(JSON)

    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class Conflict(Base):
    """Every detected sync conflict is recorded, per plan §17 ('every
    conflict should be recorded') — never silently auto-resolved without a
    trace."""

    __tablename__ = "sync_conflicts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    aggregate_type: Mapped[str] = mapped_column(String(50))
    aggregate_id: Mapped[str] = mapped_column(String(100))
    field: Mapped[str] = mapped_column(String(100))
    local_value: Mapped[str] = mapped_column(String(500))
    remote_value: Mapped[str] = mapped_column(String(500))
    resolution: Mapped[str | None] = mapped_column(String(30), nullable=True)  # USE_LOCAL|USE_REMOTE|MANUAL|PENDING
    resolved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)


class DeviceSyncState(Base):
    """Last-known sync watermark per device — drives the sync dashboard
    (plan §82: last successful sync, pending events, failed events)."""

    __tablename__ = "device_sync_state"

    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    last_sequence_received: Mapped[int] = mapped_column(Integer, default=0)
    last_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pending_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
