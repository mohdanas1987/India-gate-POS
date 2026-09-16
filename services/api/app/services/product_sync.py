"""
Phase 12 — Product/Category reconciliation & sync-loop prevention.

Pure logic, deliberately kept DB-free and dependency-free so it can be
unit tested exhaustively (this is the highest-risk feature in the whole
plan per the plan's own emphasis in §34-41). Callers (routers, sync
worker) wire this to the ORM and the WebsiteIntegrationProvider.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum


class MatchMethod(str, Enum):
    EXTERNAL_ID = "EXTERNAL_ID"
    SKU = "SKU"
    BARCODE = "BARCODE"
    UNMATCHED = "UNMATCHED"


@dataclass(frozen=True, slots=True)
class PosProductRef:
    id: int
    sku: str | None
    barcodes: tuple[str, ...]
    category_name: str | None
    price_minor: int
    name: str
    external_product_id: str | None = None  # if already mapped


@dataclass(frozen=True, slots=True)
class WebsiteProductRef:
    external_product_id: str
    sku: str | None
    barcode: str | None
    category_name: str | None
    price_minor: int
    name: str


@dataclass(frozen=True, slots=True)
class MatchResult:
    pos_product_id: int
    external_product_id: str
    method: MatchMethod


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    matched: list[MatchResult]
    pos_only: list[PosProductRef]
    website_only: list[WebsiteProductRef]
    conflicts: list[MatchResult]  # matched but a synced field differs
    excluded: list[PosProductRef]  # e.g. category == Extra

    def summary(self) -> dict[str, int]:
        return {
            "matched": len(self.matched),
            "pos_only": len(self.pos_only),
            "website_only": len(self.website_only),
            "conflicts": len(self.conflicts),
            "excluded": len(self.excluded),
        }


def is_category_sync_eligible(category_name: str | None, excluded_names: tuple[str, ...] = ("Extra",)) -> bool:
    """The Extra-category exclusion rule (plan §37), applied by name against
    the configured exclusion list — the authoritative flag actually lives
    on Category.sync_to_website in the DB; this function is what the seed
    data / any name-based fallback check uses, and what tests assert
    against directly."""
    if category_name is None:
        return True
    return category_name.strip().lower() not in {n.lower() for n in excluded_names}


def reconcile(
    pos_products: list[PosProductRef],
    website_products: list[WebsiteProductRef],
    excluded_category_names: tuple[str, ...] = ("Extra",),
) -> ReconciliationReport:
    """Plan §34-36: match by external mapping ID first, then SKU, then
    barcode. Never fuzzy-match by name alone (plan: 'never automatically
    merge uncertain products'). Extra-category products are excluded
    entirely — they never enter matched/pos_only/website_only, so the
    website side can never learn of them."""

    matched: list[MatchResult] = []
    conflicts: list[MatchResult] = []
    excluded: list[PosProductRef] = []
    eligible_pos: list[PosProductRef] = []

    website_by_id = {w.external_product_id: w for w in website_products}
    website_by_sku = {w.sku: w for w in website_products if w.sku}
    website_by_barcode = {w.barcode: w for w in website_products if w.barcode}
    matched_external_ids: set[str] = set()

    for p in pos_products:
        if not is_category_sync_eligible(p.category_name, excluded_category_names):
            excluded.append(p)
            continue
        eligible_pos.append(p)

        website_match: WebsiteProductRef | None = None
        method = MatchMethod.UNMATCHED

        if p.external_product_id and p.external_product_id in website_by_id:
            website_match = website_by_id[p.external_product_id]
            method = MatchMethod.EXTERNAL_ID
        elif p.sku and p.sku in website_by_sku:
            website_match = website_by_sku[p.sku]
            method = MatchMethod.SKU
        else:
            for code in p.barcodes:
                if code in website_by_barcode:
                    website_match = website_by_barcode[code]
                    method = MatchMethod.BARCODE
                    break

        if website_match is not None:
            matched_external_ids.add(website_match.external_product_id)
            result = MatchResult(p.id, website_match.external_product_id, method)
            if website_match.price_minor != p.price_minor or website_match.name != p.name:
                conflicts.append(result)
            else:
                matched.append(result)

    pos_only = [
        p
        for p in eligible_pos
        if p.id not in {m.pos_product_id for m in matched} and p.id not in {c.pos_product_id for c in conflicts}
    ]
    website_only = [w for w in website_products if w.external_product_id not in matched_external_ids]

    return ReconciliationReport(
        matched=matched,
        pos_only=pos_only,
        website_only=website_only,
        conflicts=conflicts,
        excluded=excluded,
    )


# --- Sync-loop prevention (plan §40-41) ---------------------------------

@dataclass(frozen=True, slots=True)
class SyncMetadata:
    source_system: str  # "POS" | "WEBSITE"
    version: int
    sync_id: str


class SyncLoopGuard:
    """Tracks the last-applied sync_id per (aggregate_type, aggregate_id)
    so an event that's just bouncing back from where it came from is
    rejected instead of being re-applied (plan §41: 'reject already
    processed events'). In production this state is the WebsiteSyncEvent
    table's unique external_event_id constraint; this in-memory version
    exists so the rule itself has a fast, DB-free unit test."""

    def __init__(self):
        self._seen_sync_ids: set[str] = set()
        self._last_version: dict[str, int] = {}

    def should_apply(self, aggregate_key: str, incoming: SyncMetadata) -> bool:
        if incoming.sync_id in self._seen_sync_ids:
            return False  # exact replay of an event we already applied
        last_version = self._last_version.get(aggregate_key, -1)
        if incoming.version <= last_version:
            return False  # stale/out-of-order update
        return True

    def record_applied(self, aggregate_key: str, incoming: SyncMetadata) -> None:
        self._seen_sync_ids.add(incoming.sync_id)
        self._last_version[aggregate_key] = incoming.version
