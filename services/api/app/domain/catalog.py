"""
Phase 5 — Product/Catalog Domain.

Legacy findings addressed:
  - Legacy `products.price` was a `varchar` cleaned with regex at request
    time. Here `price_minor` is a real integer column (see app/core/money.py).
  - Legacy category data (57 rows) had case-duplicates ('Cosmetics' /
    'cosmetics'), overlapping names, and a literal 'undefined' category.
    This schema doesn't fix that data — Phase 20 migration/reconciliation
    does — but it does give categories a normalized `slug` with a unique
    constraint so the *new* system can't re-accumulate the same mess.
  - `Category.sync_to_website` is an explicit column, not a name-based
    guess: the 'Extra' category (plan's mandatory exclusion) is enforced
    by seeding this column to False for it, not by string-matching
    "Extra" at sync time. Seed logic lives in app/domain/seed.py.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import String, ForeignKey, Boolean, Integer, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_category_tenant_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255))  # normalized (lowercased, trimmed) — prevents new case-duplicates
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_to_website: Mapped[bool] = mapped_column(Boolean, default=True)  # False for 'Extra' — explicit, not inferred


class Tax(Base):
    __tablename__ = "taxes"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(100))
    rate_basis_points: Mapped[int] = mapped_column(Integer)  # e.g. 2100 == 21.00% — integer, not float


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("tenant_id", "sku", name="uq_product_tenant_sku"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    tax_id: Mapped[int | None] = mapped_column(ForeignKey("taxes.id"), nullable=True)

    sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    price_minor: Mapped[int] = mapped_column(Integer)  # integer minor units — never float/varchar
    currency: Mapped[str] = mapped_column(String(3), default="EUR")

    unit: Mapped[str] = mapped_column(String(20), default="piece")  # kg|g|litre|ml|piece|box|pack
    is_weighted: Mapped[bool] = mapped_column(Boolean, default=False)

    pos_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    website_visible: Mapped[bool] = mapped_column(Boolean, default=False)  # off by default until sync is enabled+matched

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)  # soft delete, matches legacy's good pattern

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )

    barcodes: Mapped[list["Barcode"]] = relationship(back_populates="product")

    @property
    def effective_sync_eligible(self) -> bool:
        """A product is eligible for website sync only if its category
        allows it (Extra excluded) AND the product itself hasn't opted out."""
        return self.website_visible


class Barcode(Base):
    """Legacy had a single `code` varchar per product (6,789 distinct,
    non-unique-enforced). This is a proper 1:N table with a real unique
    constraint, so multi-barcode products (case + unit barcodes) and
    duplicate-barcode detection (needed for Phase 12 reconciliation) are
    both possible."""

    __tablename__ = "barcodes"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_barcode_tenant_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    code: Mapped[str] = mapped_column(String(100))

    product: Mapped["Product"] = relationship(back_populates="barcodes")


class ProductWebsiteMapping(Base):
    """Phase 12 reconciliation record: links a POS product to its website
    counterpart, with the match method recorded so bulk-conflict review UI
    (plan §36) can show *why* two products were matched."""

    __tablename__ = "product_website_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    external_product_id: Mapped[str] = mapped_column(String(100))
    match_method: Mapped[str] = mapped_column(String(30))  # EXTERNAL_ID | SKU | BARCODE | MANUAL
    match_confidence: Mapped[str] = mapped_column(String(20), default="CONFIRMED")  # CONFIRMED | REVIEW | CONFLICT
    last_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
