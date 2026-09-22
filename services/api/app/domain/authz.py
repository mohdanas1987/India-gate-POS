"""
Phase 3/4 — Users, RBAC, ABAC-support (ApprovalPolicy), Audit log.

Legacy findings fixed here:
  - Legacy `users.type` (default 'cashier') existed but nothing ever
    checked it. Here, Role/Permission/RolePermission are real, queried
    tables (see app/core/rbac.py) so permissions are admin-configurable,
    not hardcoded in routes.
  - AuditLog is append-only by convention (routes must never UPDATE/DELETE
    audit rows — enforced at the service layer, not by a DB trigger yet;
    a DB-level REVOKE UPDATE/DELETE is a Phase 18 hardening item).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import String, ForeignKey, DateTime, Boolean, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    roles: Mapped[list["UserRole"]] = relationship(back_populates="user")


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(100))  # e.g. Owner, Store Manager, Cashier — configurable, not an enum
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True)  # e.g. "orders.refund", "products.delete"
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id"))


class UserRole(Base):
    __tablename__ = "user_roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id"), nullable=True)  # ABAC scope

    user: Mapped["User"] = relationship(back_populates="roles")
    role: Mapped["Role"] = relationship()


class ApprovalPolicy(Base):
    """E.g. 'refund above €20 requires manager approval' — thresholds live
    in data, never hardcoded in the UI (plan §52/§53)."""

    __tablename__ = "approval_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    action_code: Mapped[str] = mapped_column(String(100))  # e.g. "orders.refund"
    threshold_minor_units: Mapped[int | None] = mapped_column(nullable=True)
    # Phase 9B correction gate (CTO review of 96f6aa9, P1 finding #8):
    # INFORMATIONAL ONLY — a human-readable label for admin UIs ("Store
    # Manager and above"), never consulted by refunds.py/discounts.py to
    # decide who can act. The actual authority is always a Permission
    # (orders.refund.override / orders.discount.override), checked via
    # principal_has_permission — a role name here can drift out of sync
    # with which permissions a role actually holds (roles are
    # admin-editable; see seed.py), so it must never become a second,
    # competing source of truth for authorization. Kept as a column
    # rather than dropped because Phase 14 (admin) will want it to render
    # a human label without joining through role_permissions.
    required_role: Mapped[str] = mapped_column(String(100))


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    # Phase 9B correction gate (CTO review of 96f6aa9, security issue #2):
    # which store this approval belongs to, so a store-scoped manager
    # can't see or resolve another store's pending approval just because
    # they share a tenant. Nullable only for data that predates this
    # column (see the migration's backfill) — every approval CREATED from
    # here on always sets it.
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id"), nullable=True)
    action_code: Mapped[str] = mapped_column(String(100))
    requested_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="REQUESTED")  # REQUESTED|APPROVED|REJECTED|EXPIRED
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    """Append-only. who/what/when/where/device/before/after/reason/approval/source (plan §54)."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(String(100))
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("approvals.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="POS")  # POS|WEBSITE|ADMIN|SYSTEM
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
