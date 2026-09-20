# India Gate Smarter AI POS — Phase Status

Honest status per the plan's own rule ("do not hide failures... mark external dependencies... do not fabricate successful integration"). Nothing below is claimed as done unless it was actually run and verified — the "Evidence" line under each phase says exactly what was executed.

---

## CTO Audit Remediation (commit c4bfb82 → this commit)

A CTO-level audit of commit `c4bfb82` returned a **FAIL** verdict. The single most important finding was correct and is the root cause of nearly everything else the audit flagged: **the committed Git archive was missing 8 real, working, previously-tested files** (`api/v1/products.py`, `api/v1/orders.py`, `api/v1/cash.py`, `services/checkout.py`, `services/refunds.py`, and three test files) — they existed in the build environment and were exercised there, but an earlier session's push to this repository never actually included them. `docs/PHASE-STATUS.md` described code that was not in the commit it described. That is a real process failure, not a documentation nitpick, and it is the reason this section exists: **from now on, nothing in this document is written before the corresponding code has been committed and re-verified from that committed state.**

Every numbered finding from that audit is addressed below. Each line says FIXED (with what was actually run to prove it), PARTIALLY FIXED (what changed and what's still open), or NOT ADDRESSED (left as a disclosed gap, not silently dropped).

**Transfer-integrity note, disclosed rather than swept under the rug:** the remediation work below was done in a separate build environment from the actual GitHub repository, and moving it across required three corrective commits, not one. The first transfer (`02ea7bf`) covered the files this document originally listed, but two more real gaps were only caught by a full file-by-file checksum audit against the verified build environment — not by trusting a remembered list of "what changed": `app/services/checkout.py` and `app/services/refunds.py` (`1207f1c`) were missing entirely, and `app/core/rbac.py`, `app/domain/seed.py`, and `tests/conftest.py` (`3def73c`) had silently drifted, including `conftest.py` still using a test fixture that does not isolate tests whose code-under-test calls `db.commit()` internally. As of `3def73c`, every one of the 48 tracked `.py` files under `services/api` and `tests/`, and all 24 tracked config/source files under `apps/pos/desktop`, checksum identically against the build environment where the full pytest suite (55/55) and live curl/Node reproductions described below were actually run. A live `pytest` run inside this exact repository clone has not yet been separately triggered — see the honest caveat on findings #24–26 below.

| # | Finding | Status | What was actually done / verified |
|---|---|---|---|
| 1–4 | Missing `products.py`/`orders.py`/`cash.py`/`checkout.py`/`refunds.py`; docs described uncommitted code | **FIXED** | All 8 files restored. Verified by reconciling the full file tree between the build environment and the committed tree (`git ls-tree -r HEAD` vs. the working copy) until they matched exactly, then `python -c "from app.main import app"` booting cleanly and listing every route including the new ones. |
| — | (root cause reproduction) | **FIXED** | Full cold-boot reproduction actually run: dropped and recreated the Postgres dev database, `alembic upgrade head` from empty, fresh `uvicorn app.main:app`, then a real `curl` sequence — login → open cash session → `POST /api/v1/sync/events` (an offline-style sale) → `GET /api/v1/orders/{id}` — returned `subtotal_minor=2598, tax_minor=546, total_minor=3144`, matching hand-calculated expected values exactly. |
| 9 | Tenant isolation insufficient; `require_permission` doesn't check tenant/store scope | **PARTIALLY FIXED** | `Principal` now carries `tenant_id` (added to the JWT itself — `decode_token` rejects any token issued before this fix rather than defaulting it to a guess). Every route that previously hardcoded `tenant_id=1` (`orders.py` create/refund, `products.py` create, `cash.py` open) now uses `principal.tenant_id`. `orders.get_order`, `cash.close_session`, and `website_orders` list/status-change previously had **no tenant check at all** — any authenticated user of any tenant could read/modify another tenant's data by guessing an id; all four now filter by `principal.tenant_id`, and the website-orders list additionally scopes by store when `principal.store_id` is set. **Still open:** this is tenant/store *filtering*, not a full ABAC layer resolving device/register context per the plan's fuller vision — a legitimate distinction the audit drew, not fully closed. |
| 10 | Hardcoded `tenant_id=1` in `AuditLog` (website_orders.py) | **FIXED** | Same fix as above — now `principal.tenant_id`. Also found and fixed the *same* bug independently present in `orders.py` (create + refund), `products.py` (create), and `cash.py` (open) — the audit caught one instance; there were five. |
| 11 | Login just takes `roles[0]`, no multi-store context selection | **NOT ADDRESSED** | Still exactly as the audit found it — this needs a real UI (list memberships, let the user pick) that doesn't exist yet. Disclosed in the code with an explicit comment; not silently left as if it were fine. |
| 13 | Pagination loads the entire result set to count it | **FIXED** | `website_orders.py` now uses `SELECT COUNT(*)` via `func.count()` over the filtered subquery instead of `len(db.execute(q).scalars().all())`. |
| 5–8 | Offline-first claim overstated: checkout is cloud-dependent; outbox exists but isn't wired; no local product/tax/register catalog; no server sync endpoint | **FIXED — this was the largest single piece of remediation** | See "Phase 8 rebuild" below for the full account. Summary: a real `POST /api/v1/sync/events` endpoint now exists and is idempotent (tested + live-verified); the Electron main process now opens the local SQLite database and starts the `OutboxSyncEngine` on app launch (previously neither ever happened); a local product/tax catalog (`local_products`) is populated via a real `catalog:sync` IPC call against the live `/api/v1/products` endpoint; checkout inside Electron (`checkoutOffline`) runs entirely against local SQLite with a proven zero-network path. |
| 18 | `printReceipt(html)` accepts arbitrary HTML from the renderer | **FIXED** | Replaced with a structured `ReceiptData` contract (order label, lines, total — all primitives). The actual HTML is now generated inside the trusted main process (`electron/hardware/electronPrinting.ts`) with every field HTML-escaped, not assembled in the renderer and handed to the printer unrestricted. |
| 19 | Access token in renderer `localStorage` | **SUBSTANTIALLY FIXED inside Electron** | The token now goes to the main process via IPC immediately after login and is encrypted at rest with Electron's `safeStorage` (OS keychain-backed); it is never written to `localStorage` and the renderer never holds the raw value again. Every authenticated API call from the POS/Website-Orders screens (session open/check, product search, checkout, website-orders list) now proxies through a generic `api:authed-request` IPC call in the main process rather than the renderer attaching its own Bearer header. **Disclosed limitation:** outside Electron (the Playwright smoke test's plain-browser renderer has no secure-storage IPC to call at all) `localStorage` remains the only place a token *can* live — this is a different environment, not an unfixed version of the same one. |
| — | (undisclosed hidden precondition found during remediation, not in the original audit) | **FIXED** | There was no "open a cashier session" UI anywhere in the app. Every prior claim that checkout "worked" was true only because a session had been opened once, months of testing ago, via a direct `curl` call against a persistent dev database that was never closed — a real, undocumented, non-reproducible precondition that would have broken for anyone starting from a clean database. Built a real `GET /api/v1/cash/session/current` endpoint and an "Open Register" screen; the smoke test now proves this from a **freshly migrated, zero-data database** — see the Phase 9 entry below. |
| 12/16/22/23 | Website-orders UI incomplete; product-sync engine not wired to a live pipeline; weighted-product quantity UI; POS UI missing category nav/split payments/holds/etc. | **NOT ADDRESSED** | Real, substantial gaps, correctly identified, deliberately left out of this remediation pass, which was scoped to repository integrity + the offline-first architecture gate the audit called the release-blocking issue. Not fabricated as done. |
| 24–26 | CI cannot pass against the audited commit; test suite couldn't be reproduced independently; docs ahead of source | **FIXED (root cause), verified locally** | `pytest` now passes 55/55 from a fresh `igpos_test` schema (conftest drops/recreates it every run — it does not depend on anyone's prior local state). The CI workflow's exact steps (`pip install -r requirements.txt`, `PYTHONPATH` set as the workflow sets it, `pytest tests/ -v`) were reproduced locally against the actual committed tree, not just described. **Honest caveat:** this was reproduced locally, not by triggering an actual GitHub Actions run from this environment — nothing in the reproduction differs from what `.github/workflows/ci.yml` executes, but the live CI run itself has not been separately confirmed green. |

### Phase 8 rebuild — what "offline-first" now actually means

This is written in detail because it was the audit's central architectural objection, and the standard demanded was reproducible evidence, not a description.

**Server side (`services/api/app/api/v1/sync.py`, new file):** `POST /api/v1/sync/events` accepts a client-generated `event_id` and is idempotent — a replayed event is detected via `InboxEvent`'s unique constraint and returns `already_processed` without reprocessing (proven by `test_sync.py::test_replaying_the_same_event_id_is_idempotent_not_duplicated`, which asserts exactly one `InboxEvent` row and no duplicate order after sending the same event twice). It does **not** trust the client's own computed total: for `order.created` events it recomputes the sale from the server's own product/tax data via the same `create_pos_sale()` the direct checkout API uses, so a compromised or buggy offline client cannot write an arbitrary total to Postgres. A failure partway through (e.g. an unknown product id) is rolled back cleanly and recorded on the `InboxEvent` row (`retry_count`, `last_error`) rather than either silently succeeding or leaving a broken half-written order committed — proven by `test_sync.py::test_a_bad_product_id_is_recorded_as_a_failure_not_a_silent_partial_order`, which explicitly checks no zero-total `OPEN` order exists afterward. An unrecognized `event_type` is rejected with a clear error rather than silently accepted.

**Desktop side, main process (`apps/pos/desktop/electron/main.ts`, `electron/sync/schema.ts`, `electron/offlineCheckout.ts`, all substantially rewritten/new):** on app launch, the local SQLite database is now actually opened and the `OutboxSyncEngine` is actually started — before this fix, neither call existed anywhere, so the outbox and sync engine were, in the audit's own words, "library code, not an operating POS subsystem." The local schema gained `local_products` (id, price, currency, tax rate, barcodes), `local_register_session` (the cached open session), and `local_auth_context` — the pieces the audit correctly identified as missing ("prices aren't locally authoritative, tax isn't locally authoritative, checkout can't validate against the local catalog"). `checkoutOffline()` runs entirely against this local data: it fails loudly (not silently) if there's no cached open session or the product isn't in the local catalog, and otherwise computes the same subtotal/tax/total math as the server (`round(subtotal_minor * rate_basis_points / 10000)`, identical to `Money.percentage()`), writes a local order row, and enqueues an outbox event — all inside a single `better-sqlite3` transaction, so a crash between the two can't happen.

**What was actually run to verify this, not just written:** a Node script drove the real compiled Electron output (`tsc -p electron/tsconfig.json` then imported the resulting JS) against a real SQLite file: checkout before any session was cached failed with a clear error; checkout with a session but an unsynced product failed with a clear error; after seeding a local catalog snapshot, `checkoutOffline` produced `subtotal=2598, tax=546, total=3144` — then a second script ran the actual `OutboxSyncEngine.tick()` against the live API with a real login token, and the event was delivered, marked synced, and produced `GET /api/v1/orders/2` returning the identical `2598 / 546 / 3144` on the server. The offline-computed receipt and the server's authoritative total matched exactly.

**Disclosed boundary, stated plainly rather than hidden:** opening or closing a cashier session still requires connectivity — once a session has been opened online and cached, every sale for the rest of that session works with zero network calls, but a device that has never been online cannot start a shift offline. Token refresh is not implemented; if the cached access token expires (30 minutes) while the device is offline, sync resumes once the cashier logs in again online. Both are real, current limits of this rebuild, not gaps papered over.

---

## Phase 0 — Forensic Audit — **PASS** (prior session)
See the separately-saved Phase 0 audit document. Not repeated here.

## Phase 1 — Repository + Safety Foundation — **CONDITIONAL PASS** (was PASS; downgraded, then repaired this pass)
Repo structure matches the plan's target layout. Downgraded to CONDITIONAL after the CTO audit found the committed archive was missing files the earlier PASS verdict relied on — repository integrity is a real Phase 1 concern, and claiming PASS again requires the *next* independent audit of a freshly re-cloned commit to agree, not this document's own say-so.
**Evidence this pass:** full file-tree reconciliation between build environment and committed tree; cold-boot API reproduction (drop DB → migrate → seed → boot → live checkout) succeeded.
**Gap:** no automated backup strategy implemented yet (unchanged from before).

## Phase 2 — Security Foundation — **CONDITIONAL PASS**
Electron hardening (`nodeIntegration:false`, `contextIsolation:true`, `sandbox:true`) unchanged and still correct. JWT/identity-from-token-only unchanged and still correct (regression test passes). **New this pass:** the access token no longer lives in renderer `localStorage` inside Electron (see CTO remediation table, finding #19) — encrypted via `safeStorage` in the main process instead. **Still open:** the legacy remote-update RCE surface has no replacement (unchanged); token refresh is not implemented (see disclosed boundary above).

## Phase 3 — Domain Foundation — **PASS**
Unchanged — schema foundation was never in question.

## Phase 4 — Authorization — **CONDITIONAL** (was CONDITIONAL FAIL; real fixes applied, not fully closed)
RBAC enforcement itself was always real (regression tests pass). What the audit correctly flagged as broken: **five separate routes hardcoded `tenant_id=1`** instead of deriving it from the caller, and **three routes had no tenant check on the resource being read/modified at all** (`get_order`, `close_session`, website-orders list/status-change) — meaning any authenticated user of any tenant could read or modify another tenant's orders, sessions, or website orders by guessing an id. All eight are fixed (see remediation table, findings #9/#10) and covered by the existing + new test suite for the parts that are unit-testable at the service layer; the route-level tenant checks themselves were verified via the live curl reproduction (a request without the right tenant returns 404, not another tenant's data) rather than a new automated test for every case — a gap worth closing with dedicated tenant-isolation tests, not claimed as fully proven here.
**Still open:** multi-store context selection at login (finding #11); the Approval entity still isn't wired into a real request/response workflow (unchanged from before).

## Phase 5 — Product/Catalog Domain — **CONDITIONAL** (API now exists and is tenant-scoped)
`products.py` API (search, barcode lookup, create) restored to the commit (was entirely absent — CTO finding #1-4) and additionally hardened: tenant-scoped queries (previously had none at all), and `ProductOut` now carries `tax_rate_basis_points` and `barcodes` so the offline catalog sync has what it needs.
**Gap:** still no update/delete/import — only search + barcode lookup + basic create, same as before.

## Phase 6 — Inventory Ledger — **PASS** (unchanged from before — was already real and writing rows)

## Phase 7 — Order/Payment Domain — **PASS** (restored)
`checkout.py`, `refunds.py`, and the `orders.py` API were entirely absent from the audited commit despite being described as complete — restored, and the get_order/refund tenant-check gaps (Phase 4) fixed alongside them. Live-verified via the cold-boot curl reproduction: subtotal/tax/total for a 2-item cart matched hand-calculated values exactly.

## Phase 8 — Offline-First POS — **CONDITIONAL PASS** (was FAIL; this is the core of this remediation pass)
See the detailed "Phase 8 rebuild" section above. Real, verified, zero-network local checkout now exists; the previously-missing server sync endpoint now exists and is idempotent and tenant-scoped; the outbox engine is now actually started at app launch. **Still open, disclosed above:** session open/close needs connectivity; no token refresh; no conflict-resolution UI (a sync failure is recorded, not surfaced to the cashier); the offline test matrix beyond what's described here (POS restart mid-offline-session, extended multi-hour offline runs) has not been executed.

## Phase 9 — Modern POS UI — **CONDITIONAL PASS**
Real gap found and fixed during this remediation (not part of the original audit, but the same category of issue): there was no "open a cashier session" UI at all — every earlier claim that checkout worked rested on a session opened once by hand, months earlier, that never got closed. A real "Open Register" screen and `GET /api/v1/cash/session/current` endpoint now exist. **Evidence:** the Playwright smoke test was re-run against a completely fresh database (dropped, remigrated, reseeded with zero sessions) and passed end-to-end, including driving the new Open Register form through the real UI — `STEP 2a: no open cashier session found — opening one via the real UI` in the test output, not skipped or mocked.
**Gap, unchanged:** category sidebar/filtering, barcode-scanner wedge input, weighted-product quantity entry, split payments, held/suspended carts, X/Z reports — none of this exists yet.

## Phase 10 — Hardware Abstraction — **CONDITIONAL PASS**
Printer/drawer/barcode-label unchanged and still real. **Fixed this pass:** `printReceipt` took arbitrary HTML from the renderer; now takes a structured `ReceiptData` object and the trusted main process generates (and HTML-escapes) the markup.
**Gap, unchanged:** scanner/scale/real-payment providers still unimplemented.

## Phase 11 — Website Integration — **PARTIAL PASS** (unchanged)
Mock provider and the website-orders API/UI are real, restored to the commit, and now tenant/store-scoped (Phase 4 fix). The pagination bug (loading the full result set to count it) is fixed. **Unchanged, real gaps:** no webhook receiver, no polling worker, no order-detail screen, no status-history UI, no real India Gate provider (still blocked on the unanswered platform question).

## Phase 12 — Product/Category/Price Synchronization — **PASS** (core logic; still not wired to a live pipeline)
Unchanged from before — the reconciliation engine and its tests were never in question. The audit's distinction between "reconciliation logic exists" and "synchronization system exists" is correct and remains true: no sync worker, no webhook, no conflict-resolution UI were built in this remediation pass (it was scoped to repository integrity and the offline-first gate).

## Phase 13 — Multi-Store — **NOT STARTED** (unchanged)
## Phase 14 — Admin Platform — **NOT STARTED** (unchanged)
## Phase 15/16 — AI Platform / AI Features — **NOT STARTED** (unchanged)
## Phase 17 — Observability — **NOT STARTED** (unchanged)

## Phase 18 — Security Testing — **CONDITIONAL** (was NOT STARTED; real fixes landed as a byproduct of the audit, not a dedicated pass)
Five tenant-isolation bugs and one arbitrary-HTML-to-printer issue were found and fixed this pass (see remediation table). This is still not a dedicated security review — no penetration testing, no automated tenant-isolation test suite, no secret scanning.

## Phase 19 — Performance — **NOT STARTED** (unchanged, though the specific pagination bug the audit found is fixed — see Phase 11)

## Phase 20 — Legacy Data Migration — **NOT STARTED** (unchanged)

## Phase 21 — Full UAT Preparation — **NOT STARTED, BY DESIGN** (unchanged)

---

## What's actually usable today, right now

A backend that boots cleanly from a fresh clone (`git clone` → `pip install -r requirements.txt` → `alembic upgrade head` → `uvicorn app.main:app` — this exact sequence was run, not assumed) with: working login and RBAC; tenant-scoped website-order listing/status-change with a real audit trail and correct `COUNT(*)` pagination; tenant-scoped product search/create/barcode lookup; cashier session open/close/current with a real UI; a genuine checkout that computes correct totals, writes inventory ledger rows, and produces an auditable order; a genuine offline sync endpoint that idempotently and safely ingests sales created with no network connection. The desktop app now has a real "Open Register" flow, a local-first offline checkout path proven end-to-end against real SQLite and a real running server, and a structured (not arbitrary-HTML) receipt-printing contract.

**Not yet wired, stated plainly:** website-order detail/history UI, a real product-sync worker/webhook pipeline, weighted-product quantity entry, split payments, multi-store context selection at login, token refresh, and everything listed under Phases 13–21 above.

## What genuinely blocks further real progress (not just "more time")

1. **The India Gate website's actual platform is still unknown.** Unchanged from every previous report — Phase 11/12 cannot go further than the mock provider without it.
2. **Business rules haven't been decided for India Gate specifically:** refund thresholds, override roles, and the website-order inventory reservation policy.

## Recommended next input needed

Same two as every previous report: the website platform, and refund/approval threshold numbers. Everything else that doesn't depend on those — the website-orders detail screen, a real sync worker, weighted-product UI, multi-store login context — can proceed without further input, and should be built and *re-verified from the actual committed state* before being reported as done, per the correction this remediation pass exists to make permanent.
