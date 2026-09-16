"""
This is the mandatory "Extra category" test the plan itself specifies in
§91, plus the reconciliation-matching and sync-loop-prevention tests from
§35/§40-41/§90/§92.
"""
from app.services.product_sync import (
    PosProductRef,
    WebsiteProductRef,
    reconcile,
    is_category_sync_eligible,
    SyncLoopGuard,
    SyncMetadata,
)


def test_extra_category_is_never_sync_eligible():
    assert is_category_sync_eligible("Extra") is False
    assert is_category_sync_eligible("extra") is False  # case-insensitive, guards against legacy case-duplicate mess
    assert is_category_sync_eligible("Extra ") is False  # trims whitespace


def test_other_categories_remain_eligible():
    assert is_category_sync_eligible("Grocery") is True
    assert is_category_sync_eligible(None) is True  # uncategorized products are not auto-excluded


def test_extra_category_product_excluded_from_reconciliation_entirely():
    """Plan §91: create an Extra->Test Product, enable sync, verify it
    never appears in POS-only/matched/website-only lists — i.e. the
    website can never learn it exists."""
    pos = [
        PosProductRef(id=1, sku="EX-1", barcodes=(), category_name="Extra", price_minor=500, name="Test Product"),
        PosProductRef(id=2, sku="GR-1", barcodes=(), category_name="Grocery", price_minor=1000, name="Basmati Rice 5kg"),
    ]
    report = reconcile(pos, website_products=[])

    assert len(report.excluded) == 1
    assert report.excluded[0].id == 1
    assert all(p.id != 1 for p in report.pos_only)
    assert all(m.pos_product_id != 1 for m in report.matched)
    assert all(m.pos_product_id != 1 for m in report.conflicts)
    # the non-Extra product IS eligible and shows up as pos_only (no website match yet)
    assert any(p.id == 2 for p in report.pos_only)


def test_match_by_external_id_priority_over_sku_and_barcode():
    pos = [
        PosProductRef(
            id=10,
            sku="SKU-A",
            barcodes=("111",),
            category_name="Grocery",
            price_minor=1000,
            name="Rice",
            external_product_id="web-10",
        )
    ]
    website = [
        WebsiteProductRef("web-10", sku="SKU-DIFFERENT", barcode="999", category_name="Grocery", price_minor=1000, name="Rice"),
        WebsiteProductRef("web-99", sku="SKU-A", barcode="111", category_name="Grocery", price_minor=1000, name="Rice (wrong match)"),
    ]
    report = reconcile(pos, website)
    assert len(report.matched) == 1
    assert report.matched[0].external_product_id == "web-10"
    assert report.matched[0].method.value == "EXTERNAL_ID"


def test_match_by_sku_when_no_external_id():
    pos = [PosProductRef(id=11, sku="SKU-B", barcodes=(), category_name="Grocery", price_minor=500, name="Flour")]
    website = [WebsiteProductRef("web-1", sku="SKU-B", barcode=None, category_name="Grocery", price_minor=500, name="Flour")]
    report = reconcile(pos, website)
    assert report.matched[0].method.value == "SKU"


def test_match_by_barcode_when_no_sku_match():
    pos = [PosProductRef(id=12, sku=None, barcodes=("123456",), category_name="Grocery", price_minor=200, name="Onion")]
    website = [WebsiteProductRef("web-2", sku=None, barcode="123456", category_name="Grocery", price_minor=200, name="Onion")]
    report = reconcile(pos, website)
    assert report.matched[0].method.value == "BARCODE"


def test_price_mismatch_is_a_conflict_not_a_silent_overwrite():
    pos = [PosProductRef(id=13, sku="SKU-C", barcodes=(), category_name="Grocery", price_minor=1000, name="Sugar")]
    website = [WebsiteProductRef("web-3", sku="SKU-C", barcode=None, category_name="Grocery", price_minor=1200, name="Sugar")]
    report = reconcile(pos, website)
    assert len(report.matched) == 0
    assert len(report.conflicts) == 1


def test_unmatched_pos_product_reported_as_pos_only():
    pos = [PosProductRef(id=14, sku="SKU-D", barcodes=(), category_name="Grocery", price_minor=1000, name="Ghee")]
    report = reconcile(pos, website_products=[])
    assert len(report.pos_only) == 1
    assert report.pos_only[0].id == 14


def test_unmatched_website_product_reported_as_website_only():
    website = [WebsiteProductRef("web-5", sku="SKU-E", barcode=None, category_name="Grocery", price_minor=1000, name="Turmeric")]
    report = reconcile(pos_products=[], website_products=website)
    assert len(report.website_only) == 1


# --- Sync loop prevention (plan §92: no infinite POS<->website loop) ----

def test_sync_loop_guard_rejects_exact_replay():
    guard = SyncLoopGuard()
    meta = SyncMetadata(source_system="POS", version=1, sync_id="evt-1")
    assert guard.should_apply("product:1", meta) is True
    guard.record_applied("product:1", meta)
    assert guard.should_apply("product:1", meta) is False  # same event replayed


def test_sync_loop_guard_rejects_stale_version_but_allows_newer():
    guard = SyncLoopGuard()
    v1 = SyncMetadata(source_system="POS", version=1, sync_id="evt-1")
    v2_stale = SyncMetadata(source_system="WEBSITE", version=1, sync_id="evt-2")  # bounced back, same version
    v3_newer = SyncMetadata(source_system="WEBSITE", version=2, sync_id="evt-3")

    guard.record_applied("product:1", v1)
    assert guard.should_apply("product:1", v2_stale) is False
    assert guard.should_apply("product:1", v3_newer) is True


def test_price_change_loop_does_not_bounce_forever():
    """Simulates plan §92's exact scenario: POS 10->12, website eventually
    12; then website 12->13, POS eventually 13; no infinite bounce."""
    guard = SyncLoopGuard()
    key = "product:99"

    # POS changes price to 12, pushes to website as version 1
    pos_change = SyncMetadata(source_system="POS", version=1, sync_id="pos-evt-1")
    assert guard.should_apply(key, pos_change)
    guard.record_applied(key, pos_change)

    # Website echoes the same change back (as it would if it just accepted
    # the push and re-broadcast) — must be rejected, not re-applied to POS.
    echo = SyncMetadata(source_system="WEBSITE", version=1, sync_id="pos-evt-1")
    assert guard.should_apply(key, echo) is False

    # Later, a genuinely new website-originated change to 13 (version 2) must go through.
    website_change = SyncMetadata(source_system="WEBSITE", version=2, sync_id="web-evt-1")
    assert guard.should_apply(key, website_change) is True
