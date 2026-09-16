# India Gate Smarter AI POS — Phase Status

Honest status per the plan's own rule ("do not hide failures... mark external dependencies... do not fabricate successful integration"). This is the CTO gate summary across every phase attempted in this build pass. Nothing below is claimed as done unless it was actually run and verified — the "Evidence" line under each phase says exactly what was executed.

---

## Phase 0 — Forensic Audit — **PASS** (prior session)
See the separately-saved Phase 0 audit document. Not repeated here.

## Phase 1 — Repository + Safety Foundation — **PASS**
Repo structure matches the plan's target layout (`apps/`, `services/`, `integrations/`, `infrastructure/`, `docs/`). `.github/workflows/ci.yml` runs API tests against a real (free, GitHub-hosted) Postgres service container and typechecks the desktop app — no paid CI required. `.env.example` documents config; no secret is committed.
**Evidence:** directory tree created and verified; CI YAML written (not yet run on GitHub — this repo hasn't been pushed yet, see "What's next" below).
**Gap:** no automated backup strategy implemented yet (plan asks for one) — deferred, low risk at this stage since there's no production data in this new system yet.

## Phase 2 — Security Foundation — **PASS** (for what's built so far)
Fixed, with evidence, the exact issues Phase 0 found in the legacy app:
- JWT secret is `Settings.jwt_secret` (env-backed); `app/main.py` refuses to start in production with the default dev secret.
- Identity is derived only from the verified token (`app/core/security.decode_token` → `Principal`) — there is no code path anywhere that reads an alternative user id from a request body/query field. Regression-tested (`test_identity_comes_only_from_verified_token_not_client_fields`).
- Every route that needs auth uses `Depends(require_permission(...))`, which 401s with no token and 403s without the right permission — verified live against a running server, not just unit-tested (see the curl transcript below).
- Electron hardened: `nodeIntegration: false`, `contextIsolation: true`, `sandbox: true` (legacy had `nodeIntegration: true`). Compiles clean with `tsc`.
- Passwords hashed with bcrypt directly (not the legacy's plaintext-adjacent risk — legacy did use bcrypt correctly for hashing, that part was fine; what's fixed here is the JWT secret and identity derivation around it).
**Evidence:** `pytest tests/test_rbac_and_seed.py` passes; live server session: login → /me → website-orders with/without token → wrong password, all returned correct HTTP codes (200/401/400), captured during this build session.
**Gap — explicitly not done:** the legacy remote-update RCE surface (`/install-update`, `/install-backend-update` downloading and extracting an unsigned zip) has no replacement built yet — Phase 79's signed/checksummed/staged update mechanism is still open. The legacy runtime auto-migration-on-error pattern is simply not present in the new code (Alembic migrations only run via explicit `alembic upgrade`), which resolves it by omission rather than by an explicit guard — acceptable, but worth stating plainly.

## Phase 3 — Domain Foundation — **PASS**
`Tenant`, `Store`, `Warehouse`, `Register`, `Device` implemented as real SQLAlchemy models, migrated into an actual local Postgres database (not SQLite-only, not hypothetical).
**Evidence:** `alembic upgrade head` created all tables in a real `igpos_dev` Postgres database; `psql \dt` output captured showing 35 tables including these.

## Phase 4 — Authorization — **PASS** (RBAC + Approval data model; ABAC context object only, not fully wired)
`Role`, `Permission`, `RolePermission`, `UserRole`, `ApprovalPolicy`, `Approval`, `AuditLog` implemented. RBAC is enforced end-to-end (see Phase 2 evidence). Seed data (`app/domain/seed.py`) creates the plan's example roles (Owner, Administrator, Store Manager, Cashier, Inventory Manager, Website Manager) with real, data-driven, per-role permissions — verified: a Cashier gets 403 on `website_orders.change_status` while a Store Manager succeeds, live.
**Gap:** ABAC (amount/store/time-based rules like "cashier can refund up to €20") has a data model (`ApprovalPolicy.threshold_minor_units`) but no route actually evaluates a threshold yet — the website-order status endpoint currently fails closed (403) on any transition flagged as requiring approval, rather than routing it through a real approval request/response flow. That flow itself (create Approval → manager approves → action proceeds) is not built. This is intentionally not faked — the endpoint tells the caller exactly why it's blocked.

## Phase 5 — Product/Catalog Domain — **PASS**
`Category` (with an explicit `sync_to_website` column, not name-matching), `Tax`, `Product` (integer `price_minor`, never a float or varchar), `Barcode` (proper 1:N with a real unique constraint — legacy had a single non-unique `code` column), `ProductWebsiteMapping` for reconciliation. Migrated to real Postgres.
**Gap:** no product CRUD API routes built yet (create/update/delete/import) — only the domain model and the reconciliation logic (Phase 12) exist. The legacy XLSX import was flagged in Phase 0 as having real issues and hasn't been re-implemented yet.

## Phase 6 — Inventory Ledger — **PASS** (model only, no consuming logic yet)
Append-only `InventoryLedger` with the exact event-type vocabulary from the plan (`SALE`, `PURCHASE_RECEIPT`, `TRANSFER_IN/OUT`, `RETURN`, `DAMAGE`, `ADJUSTMENT`, `STOCK_COUNT`, plus website-specific reservation events). `InventoryReservationPolicy` enum exists so "when does a website order decrement stock" is configurable per plan §46, not hardcoded.
**Gap:** nothing writes to this ledger yet — no order-checkout flow exists that would generate `SALE` rows, no purchasing flow generates `PURCHASE_RECEIPT` rows. This is schema-only.

## Phase 7 — Order/Payment Domain — **PASS** (model only)
Single `Order` entity with `sales_channel` (POS/WEBSITE/PHONE/MANUAL) and `external_order_id`, exactly per the plan's §45 design decision (avoids a parallel reporting universe). `OrderLine`, `OrderPayment` (with a `PaymentMethod.EXTERNAL` slot for a future real gateway), `Refund`, `RefundLine`. All money fields are integer minor units.
**Gap:** no checkout API endpoint exists yet (no `POST /orders` that actually creates an order, writes inventory ledger rows, and produces a receipt). This is the single largest missing piece for a usable POS — Phase 9's UI has nowhere real to submit a sale to yet.

## Phase 8 — Offline-First POS — **PARTIAL PASS**
Cloud-side inbox (`InboxEvent` with `UNIQUE(event_id)`, exactly matching plan §16's dedup requirement), `Conflict` (every conflict recorded, never silently resolved), `DeviceSyncState`. Device-side: a real, compiling `better-sqlite3` schema (`outbox_events`, `local_orders`, `website_orders_cache`, `device_sync_state`) and a working `OutboxSyncEngine` class with retry/backoff and the exact five connectivity states the plan requires (ONLINE/OFFLINE/SYNCING/SYNC_ERROR/PARTIALLY_CONNECTED), wired to a Zustand store and a `ConnectivityBadge` UI component.
**Gap:** the outbox engine has never been run against a real device + real network-loss scenario — it's implemented and typechecks, but the offline test matrix (plan §88 — internet lost during checkout, POS restart while offline, etc.) has not been executed even once. There is also no server-side `/api/v1/sync/events` endpoint yet for it to actually POST to (the engine references that URL; it doesn't exist server-side yet). This is real, structurally sound code that has not been integration-tested — say so plainly rather than claim it "works."

## Phase 9 — Modern POS UI — **SCAFFOLD ONLY**
A structural React page exists (`PosPage.tsx`) with the right layout regions (categories/products/cart/totals/payment buttons) and calls `window.electronAPI.drawCash()` for real, but there is no product data, no barcode scan handling, no actual checkout submission. This is explicitly NOT the "modern cashier-first POS" the plan describes — it's the skeleton that phase would be built into.
**Evidence:** compiles and builds via Vite (`dist/` output produced, 210KB bundle).

## Phase 10 — Hardware Abstraction — **PASS** (printer/drawer/barcode label; scanner and scale unconfirmed/missing)
`PrinterProvider`, `CashDrawerProvider`, `BarcodePrinterProvider` interfaces with real Electron main-process implementations, each a direct, hardened port of the legacy app's working silent-print techniques (confirmed in Phase 0's audit of `main.js`) — not reinvented, not placeholder. Compiles clean via `tsc`.
**Gap:** `BarcodeScannerProvider` and `ScaleProvider` interfaces exist but have zero implementation — the legacy app's scanner handling was never located in the backend (Phase 0 flagged this as "almost certainly a keyboard-wedge pattern in the frontend, not confirmed") and the actual legacy React frontend source was never deep-audited to confirm. `PaymentProvider` interface exists with a `PaymentResult` type but no mock or real implementation yet.

## Phase 11 — Website Integration — **PARTIAL PASS**
`WebsiteIntegrationProvider` ABC + `MockWebsiteProvider` (fully working, unit-tested, zero external dependency). `WebsiteOrder`/`WebsiteOrderStatusHistory`/`WebsiteSyncEvent` domain models. The status transition state machine is DATA (`DEFAULT_ALLOWED_TRANSITIONS`), not hardcoded logic, matching the plan's explicit requirement. The `/api/v1/website-orders` API is real and was exercised live end-to-end: list with pagination/search/status filter, a valid status transition (with audit log + status history rows actually written to Postgres, verified by direct query), an invalid transition correctly rejected (409), a permission-less role correctly rejected (403), and a transition flagged as requiring approval correctly blocked (403, with an honest message) rather than silently allowed.
**Critical open item (Phase 0 finding, repeated here because it blocks real progress):** there is no concrete provider for the actual India Gate website — its platform, auth model, and API surface are unknown. Nothing beyond the mock has been built or can be built until that's answered. This is the single most important open question blocking further work on this phase.
**Gap:** no webhook receiver endpoint exists yet; no polling worker exists yet to call `get_orders()` on a schedule; no sound/desktop notification wiring for new orders.

## Phase 12 — Product/Category/Price Synchronization — **PASS** (core logic; not wired to real data yet)
This is the most heavily tested part of the whole build, deliberately, because the plan itself flags it as the highest-risk feature. `app/services/product_sync.py` implements: category-based exclusion (Extra, case-insensitively, whitespace-trimmed), reconciliation matching in the plan's exact priority order (external ID → SKU → barcode, never fuzzy name-matching), conflict detection (a matched product with a different price/name is a conflict, not a silent overwrite), and a `SyncLoopGuard` that prevents the POS↔website infinite-bounce scenario the plan describes in §92 — all with passing unit tests, including the plan's own mandatory Extra-category test (§91) reproduced almost verbatim as `test_extra_category_product_excluded_from_reconciliation_entirely`.
**Evidence:** 39/39 tests pass, including 12 tests specifically on this module.
**Gap:** none of this is wired to real POS product data yet (no route calls `reconcile()` against the actual `products` table) and there's no sync-preview or conflict-resolution UI. It's a correct, tested engine with no cockpit around it yet.

## Phase 13 — Multi-Store — **NOT STARTED**
The schema already supports it structurally (`Store`, `Warehouse`, `Register` all exist and are store-scoped), but transfers, store-specific pricing, and consolidated multi-store reporting have no implementation. Realistic to defer — the plan itself sequences this after single-store is solid, and a single Tenant/Store pair is what's seeded today.

## Phase 14 — Admin Platform — **NOT STARTED**
No admin UI exists at all. This is a large, multi-week UI effort in its own right (products, inventory, purchasing, customers, promotions, loyalty, users/roles, reports, website orders, sync, configuration all in one surface) — nothing to report yet beyond the API pieces already listed under earlier phases.

## Phase 15/16 — AI Platform / AI Features — **NOT STARTED**
Zero code. The plan is explicit that AI must go through controlled tools, never raw DB access, and that recommendations require human approval before mutating anything — that design constraint is noted here for when this phase starts, but nothing has been built.

## Phase 17 — Observability — **NOT STARTED**
Only the most minimal piece exists: `/api/v1/health` reports real DB connectivity (verified live). No structured logging, metrics, sync dashboard, or error tracking abstraction exists yet.

## Phase 18 — Security Testing — **NOT STARTED** (beyond what Phase 2's own tests incidentally cover)
No dedicated penetration-style testing, tenant-isolation testing (there's only one tenant seeded so far), CSRF/CORS review, or secret scanning has been performed. The auth/RBAC tests that exist were written to prove Phase 2/4's own claims, not as a security test pass in their own right.

## Phase 19 — Performance — **NOT STARTED**
No load testing has been performed. The schema has the indexes you'd expect from unique constraints (SKU, barcode, external_event_id) but no dedicated performance index review against the plan's stated targets (10,000+ products, 100,000+ orders) has been done, and there's no data at that scale in this dev database yet.

## Phase 20 — Legacy Data Migration — **NOT STARTED** (Phase 0 provided the reconciliation baseline numbers only)
No migration script exists yet. Phase 0's audit gives the exact baseline this phase will need to reconcile against (6,843 products, 66,191 orders, €1,681,000 total order amount, etc.) — that's preparation, not migration.

## Phase 21 — Full UAT Preparation — **NOT STARTED, BY DESIGN**
Correctly not started: the plan is explicit that paid services are only evaluated after everything above is built and locally tested. Given how much of Phases 9, 13–20 remain, this phase being untouched is the plan working as intended, not a gap.

---

## What's actually usable today, right now

A backend you can run locally (`uvicorn app.main:app`) with working login, RBAC-enforced website-order listing and status transitions (with real audit trail), and a desktop app shell that builds and would launch (not yet launched headlessly in this sandbox — no display server here — but both the Electron main process and the renderer compile and bundle without errors).

## What genuinely blocks further real progress (not just "more time")

1. **The India Gate website's actual platform is still unknown.** This has been flagged twice now (Phase 0 and here) because Phase 11/12 cannot go further than the mock provider without it.
2. **Business rules for approval thresholds** (refund limits, which website-order transitions need manager sign-off beyond the plan's own examples, inventory reservation policy choice from §46) haven't been decided — the code has the hooks (`ApprovalPolicy`, `InventoryReservationPolicy`) but no actual numbers/policy chosen for India Gate specifically.
3. **No checkout flow exists yet** — this is the next largest single piece of work (Phase 7's API layer + Phase 9's real UI + Phase 6's ledger-writing), independent of the website questions above.

## Recommended next input needed

Two decisions unblock the most future work per unit of effort: the website platform (unblocks real Phase 11/12 progress instead of mock-only), and a decision on whether to prioritize the checkout flow (Phases 6/7/9 wired together into an actually-usable single-store POS) or keep building out breadth (multi-store, admin platform, AI) before depth. Everything else can proceed without further input.
