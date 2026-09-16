"""Phase 3 — Domain Foundation: Tenant, Store, Warehouse, Register, Device."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import String, ForeignKey, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Tenant(Base):
    """Top-level owner. A single-store India Gate deployment is one Tenant
    with one Store; multi-store (Phase 13) adds more Store rows, not more
    Tenants."""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    stores: Mapped[list["Store"]] = relationship(back_populates="tenant")


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Amsterdam")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="stores")
    registers: Mapped[list["Register"]] = relationship(back_populates="store")


class Warehouse(Base):
    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    name: Mapped[str] = mapped_column(String(255))
    is_default: Mapped[bool] = mapped_column(Boolean, default=True)


class Register(Base):
    """A physical checkout counter/till within a store."""

    __tablename__ = "registers"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    store: Mapped["Store"] = relationship(back_populates="registers")


class Device(Base):
    """A specific installed POS client (Electron instance). Used as the
    `device_id` on outbox events (Phase 8) so sync can be traced per
    machine, matching the plan's DeviceSyncState concept."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    register_id: Mapped[int | None] = mapped_column(ForeignKey("registers.id"), nullable=True)
    label: Mapped[str] = mapped_column(String(255))
    fingerprint: Mapped[str] = mapped_column(String(255), unique=True)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
