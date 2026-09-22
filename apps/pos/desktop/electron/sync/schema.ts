/**
 * Phase 8 — local (device-side) offline-first schema.
 *
 * This SQLite database is authoritative for offline operation (plan §70:
 * "the local SQLite database is authoritative for offline operation").
 * `outbox_events` is what makes checkout never block on connectivity: a
 * sale commits locally and queues an outbox row in the SAME transaction;
 * a background loop (outboxSync.ts) drains it whenever the API is
 * reachable, with retry/backoff and a UNIQUE(event_id) so replay from a
 * partial network failure doesn't duplicate the sale on the server side
 * (plan §16).
 */
import Database from "better-sqlite3";

export function openLocalDb(filePath: string): Database.Database {
  const db = new Database(filePath);
  db.pragma("journal_mode = WAL"); // survives app crash mid-write, standard for POS-grade local SQLite use

  db.exec(`
    CREATE TABLE IF NOT EXISTS outbox_events (
      event_id TEXT PRIMARY KEY,
      aggregate_type TEXT NOT NULL,
      aggregate_id TEXT NOT NULL,
      sequence INTEGER NOT NULL,
      event_type TEXT NOT NULL,
      payload TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      synced_at TEXT,
      retry_count INTEGER NOT NULL DEFAULT 0,
      last_error TEXT,
      next_attempt_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS local_orders (
      id TEXT PRIMARY KEY,
      status TEXT NOT NULL,
      total_minor INTEGER NOT NULL,
      currency TEXT NOT NULL DEFAULT 'EUR',
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      payload TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS website_orders_cache (
      id TEXT PRIMARY KEY,
      external_order_number TEXT NOT NULL,
      status TEXT NOT NULL,
      pending_sync INTEGER NOT NULL DEFAULT 0,
      payload TEXT NOT NULL,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS device_state (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );

    -- CTO audit finding (commit c4bfb82, #8): "the current POS does not
    -- maintain a local product catalog capable of operating independently
    -- ... product search can't reliably work, prices aren't locally
    -- authoritative, tax isn't locally authoritative, checkout can't
    -- validate against the local catalog." These tables are the fix:
    -- a real local snapshot of exactly what a sale needs, refreshed via
    -- catalog:sync whenever the app is online (see electron/main.ts).
    CREATE TABLE IF NOT EXISTS local_products (
      id INTEGER PRIMARY KEY,          -- same id as the server Product row
      name TEXT NOT NULL,
      sku TEXT,
      price_minor INTEGER NOT NULL,
      currency TEXT NOT NULL DEFAULT 'EUR',
      unit TEXT NOT NULL DEFAULT 'piece',
      is_weighted INTEGER NOT NULL DEFAULT 0,
      category_id INTEGER,             -- Phase 9A: same server Category id, NULL if uncategorized
      tax_rate_basis_points INTEGER,   -- NULL = no tax, matches server's tax_id NULL case
      barcodes TEXT NOT NULL DEFAULT '[]',  -- JSON array of barcode strings
      synced_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_local_products_name ON local_products(name);
    CREATE INDEX IF NOT EXISTS idx_local_products_sku ON local_products(sku);
    CREATE INDEX IF NOT EXISTS idx_local_products_category ON local_products(category_id);

    -- Phase 9A: the category sidebar needs to work OFFLINE too (per the
    -- CTO plan's own Phase 9A offline acceptance criteria — "cached
    -- catalog -> product search -> add product ... must work without HTTP
    -- dependency"), so category names/slugs are synced down alongside
    -- the product catalog, not fetched live from GET /api/v1/categories
    -- every time the sidebar renders.
    CREATE TABLE IF NOT EXISTS local_categories (
      id INTEGER PRIMARY KEY,          -- same id as the server Category row
      name TEXT NOT NULL,
      slug TEXT NOT NULL,
      synced_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- Holds at most one row: the currently-open cashier session. Cached
    -- from a successful (online) /api/v1/cash/session/open call, OR
    -- (Phase 22.1) opened locally while genuinely offline — see
    -- electron/offlineShift.ts. This is what lets checkout validate "is
    -- there an open session" without a network round-trip either way.
    -- pending_sync = 1 and session_id = 0 together mean "opened
    -- offline, the real server-side CashierSession id is not known yet" —
    -- checkout doesn't need the real id, only register/store/tenant, so
    -- it works identically either way.
    CREATE TABLE IF NOT EXISTS local_register_session (
      id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton row
      session_id INTEGER NOT NULL,
      register_id INTEGER NOT NULL,
      store_id INTEGER NOT NULL,
      tenant_id INTEGER NOT NULL,
      cashier_user_id INTEGER NOT NULL,
      opened_at TEXT NOT NULL,
      pending_sync INTEGER NOT NULL DEFAULT 0
    );

    -- Cached auth context (from the last successful login) so the app
    -- knows which tenant/store/user is operating this device without
    -- calling the server, and so an offline checkout's outbox event
    -- carries the right ids.
    CREATE TABLE IF NOT EXISTS local_auth_context (
      id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton row
      user_id INTEGER NOT NULL,
      tenant_id INTEGER NOT NULL,
      store_id INTEGER,
      role TEXT NOT NULL,
      cached_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- Phase 22.1 (offline shift-start — CTO gate: "device starts offline,
    -- cashier wants to open shift, checkout" was previously unsupported).
    -- A locally-verifiable credential, cached ONLY after a real successful
    -- online login — never fetched from or synced to the server as a
    -- separate step, and never the server's own password hash: this is a
    -- fresh bcrypt hash of the plaintext password computed HERE, in the
    -- main process, the moment online login succeeds (see
    -- electron/offlineShift.ts::cacheOfflineCredential). That means an
    -- offline login can only ever succeed for a device that has
    -- previously proven itself online with the real server — it is not a
    -- new, weaker credential store.
    CREATE TABLE IF NOT EXISTS offline_credential (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      email TEXT NOT NULL,
      password_hash TEXT NOT NULL,
      cached_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- The register this device has actually been proven, ONLINE, to be
    -- authorized for (via resolve_authorized_register on the server, the
    -- last time /api/v1/cash/session/open succeeded here). Offline shift
    -- start is only ever allowed to reopen THIS register — never an
    -- arbitrary register_id a cashier might type in, since there is no
    -- way to verify tenant/store/register ownership without the server.
    CREATE TABLE IF NOT EXISTS local_known_register (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      register_id INTEGER NOT NULL,
      known_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);

  // Phase 8.5 (CTO audit of 0cfd8ca, finding #18): next_attempt_at is new
  // as of this fix. An existing local database created before this change
  // already has an outbox_events table WITHOUT this column — SQLite's
  // CREATE TABLE IF NOT EXISTS above is a no-op against it, so this
  // migration check adds the column on top of whatever already exists.
  // SQLite has no "ADD COLUMN IF NOT EXISTS", hence the manual PRAGMA check.
  const existingColumns = db.prepare(`PRAGMA table_info(outbox_events)`).all() as { name: string }[];
  if (!existingColumns.some((c) => c.name === "next_attempt_at")) {
    db.exec(`ALTER TABLE outbox_events ADD COLUMN next_attempt_at TEXT NOT NULL DEFAULT (datetime('now'))`);
  }

  // Phase 22.1: same situation for local_register_session on a database
  // created before offline shift-start existed.
  const sessionColumns = db.prepare(`PRAGMA table_info(local_register_session)`).all() as { name: string }[];
  if (!sessionColumns.some((c) => c.name === "pending_sync")) {
    db.exec(`ALTER TABLE local_register_session ADD COLUMN pending_sync INTEGER NOT NULL DEFAULT 0`);
  }

  // Phase 9A: same situation for local_products on a database created
  // before the category sidebar existed.
  const productColumns = db.prepare(`PRAGMA table_info(local_products)`).all() as { name: string }[];
  if (!productColumns.some((c) => c.name === "category_id")) {
    db.exec(`ALTER TABLE local_products ADD COLUMN category_id INTEGER`);
    db.exec(`CREATE INDEX IF NOT EXISTS idx_local_products_category ON local_products(category_id)`);
  }

  return db;
}

export interface LocalProductRow {
  id: number;
  name: string;
  sku: string | null;
  price_minor: number;
  currency: string;
  unit: string;
  is_weighted: number;
  category_id: number | null;
  tax_rate_basis_points: number | null;
  barcodes: string; // JSON-encoded string[]
}

export function replaceLocalCatalog(db: Database.Database, products: LocalProductRow[]): void {
  // Full snapshot replace inside one transaction: simpler and safer than
  // diffing for a catalog this size, and it's exactly how a "pull the
  // whole pos_visible catalog" sync is meant to behave — a product that
  // was deleted or hidden server-side simply won't be in the new snapshot.
  const tx = db.transaction((rows: LocalProductRow[]) => {
    db.prepare(`DELETE FROM local_products`).run();
    const insert = db.prepare(
      `INSERT INTO local_products (id, name, sku, price_minor, currency, unit, is_weighted, category_id, tax_rate_basis_points, barcodes)
       VALUES (@id, @name, @sku, @price_minor, @currency, @unit, @is_weighted, @category_id, @tax_rate_basis_points, @barcodes)`
    );
    for (const row of rows) insert.run(row);
  });
  tx(products);
}

export interface LocalCategoryRow {
  id: number;
  name: string;
  slug: string;
}

export function replaceLocalCategories(db: Database.Database, categories: LocalCategoryRow[]): void {
  const tx = db.transaction((rows: LocalCategoryRow[]) => {
    db.prepare(`DELETE FROM local_categories`).run();
    const insert = db.prepare(`INSERT INTO local_categories (id, name, slug) VALUES (@id, @name, @slug)`);
    for (const row of rows) insert.run(row);
  });
  tx(categories);
}

export interface LocalCategoryWithCount extends LocalCategoryRow {
  product_count: number;
}

export function listLocalCategories(db: Database.Database): LocalCategoryWithCount[] {
  // Mirrors GET /api/v1/categories's own count query (POS-sellable
  // products only) so the sidebar shows the same numbers online and
  // offline — see app/api/v1/categories.py's list_categories().
  return db
    .prepare(
      `SELECT c.id, c.name, c.slug, COUNT(p.id) AS product_count
       FROM local_categories c
       LEFT JOIN local_products p ON p.category_id = c.id
       GROUP BY c.id, c.name, c.slug
       ORDER BY c.name`
    )
    .all() as LocalCategoryWithCount[];
}

export function searchLocalProducts(
  db: Database.Database,
  query: string | null,
  categoryId: number | null = null,
  limit = 50
): LocalProductRow[] {
  const clauses: string[] = [];
  const params: (string | number)[] = [];

  if (query) {
    const like = `%${query}%`;
    // Phase 9A: matches name/SKU/barcode, mirroring the server's
    // GET /api/v1/products search (app/api/v1/products.py) so a barcode
    // scanner's keyboard-wedge input works identically online and
    // offline — previously this only ever matched name/sku here too.
    clauses.push(`(name LIKE ? OR sku LIKE ? OR barcodes LIKE ?)`);
    params.push(like, like, like);
  }
  if (categoryId !== null) {
    clauses.push(`category_id = ?`);
    params.push(categoryId);
  }

  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  params.push(limit);
  return db
    .prepare(`SELECT * FROM local_products ${where} ORDER BY name LIMIT ?`)
    .all(...params) as LocalProductRow[];
}

export function getLocalProduct(db: Database.Database, id: number): LocalProductRow | undefined {
  return db.prepare(`SELECT * FROM local_products WHERE id = ?`).get(id) as LocalProductRow | undefined;
}

export interface LocalAuthContext {
  user_id: number;
  tenant_id: number;
  store_id: number | null;
  role: string;
}

export function saveLocalAuthContext(db: Database.Database, ctx: LocalAuthContext): void {
  db.prepare(
    `INSERT INTO local_auth_context (id, user_id, tenant_id, store_id, role, cached_at)
     VALUES (1, @user_id, @tenant_id, @store_id, @role, datetime('now'))
     ON CONFLICT(id) DO UPDATE SET user_id=excluded.user_id, tenant_id=excluded.tenant_id,
       store_id=excluded.store_id, role=excluded.role, cached_at=excluded.cached_at`
  ).run(ctx);
}

export function getLocalAuthContext(db: Database.Database): LocalAuthContext | undefined {
  return db.prepare(`SELECT user_id, tenant_id, store_id, role FROM local_auth_context WHERE id = 1`).get() as
    | LocalAuthContext
    | undefined;
}

export interface LocalRegisterSession {
  session_id: number;
  register_id: number;
  store_id: number;
  tenant_id: number;
  cashier_user_id: number;
  opened_at: string;
  pending_sync?: number; // 1 = opened offline, server-side CashierSession id not confirmed yet
}

export function saveLocalRegisterSession(db: Database.Database, s: LocalRegisterSession): void {
  db.prepare(
    `INSERT INTO local_register_session (id, session_id, register_id, store_id, tenant_id, cashier_user_id, opened_at, pending_sync)
     VALUES (1, @session_id, @register_id, @store_id, @tenant_id, @cashier_user_id, @opened_at, @pending_sync)
     ON CONFLICT(id) DO UPDATE SET session_id=excluded.session_id, register_id=excluded.register_id,
       store_id=excluded.store_id, tenant_id=excluded.tenant_id, cashier_user_id=excluded.cashier_user_id,
       opened_at=excluded.opened_at, pending_sync=excluded.pending_sync`
  ).run({ ...s, pending_sync: s.pending_sync ?? 0 });
}

export function getLocalRegisterSession(db: Database.Database): LocalRegisterSession | undefined {
  return db.prepare(`SELECT * FROM local_register_session WHERE id = 1`).get() as LocalRegisterSession | undefined;
}

export function clearLocalRegisterSession(db: Database.Database): void {
  db.prepare(`DELETE FROM local_register_session WHERE id = 1`).run();
}

export function nextSequence(db: Database.Database): number {
  const row = db.prepare(`SELECT value FROM device_state WHERE key = 'outbox_sequence'`).get() as
    | { value: string }
    | undefined;
  const next = (row ? parseInt(row.value, 10) : 0) + 1;
  db.prepare(
    `INSERT INTO device_state (key, value) VALUES ('outbox_sequence', ?)
     ON CONFLICT(key) DO UPDATE SET value = excluded.value`
  ).run(String(next));
  return next;
}

// --- Phase 22.1: offline shift-start primitives ---

export interface OfflineCredential {
  email: string;
  password_hash: string;
}

export function saveOfflineCredential(db: Database.Database, cred: OfflineCredential): void {
  db.prepare(
    `INSERT INTO offline_credential (id, email, password_hash, cached_at)
     VALUES (1, @email, @password_hash, datetime('now'))
     ON CONFLICT(id) DO UPDATE SET email=excluded.email, password_hash=excluded.password_hash, cached_at=excluded.cached_at`
  ).run(cred);
}

export function getOfflineCredential(db: Database.Database): OfflineCredential | undefined {
  return db.prepare(`SELECT email, password_hash FROM offline_credential WHERE id = 1`).get() as
    | OfflineCredential
    | undefined;
}

export function saveKnownRegister(db: Database.Database, registerId: number): void {
  db.prepare(
    `INSERT INTO local_known_register (id, register_id, known_at)
     VALUES (1, ?, datetime('now'))
     ON CONFLICT(id) DO UPDATE SET register_id=excluded.register_id, known_at=excluded.known_at`
  ).run(registerId);
}

export function getKnownRegisterId(db: Database.Database): number | undefined {
  const row = db.prepare(`SELECT register_id FROM local_known_register WHERE id = 1`).get() as
    | { register_id: number }
    | undefined;
  return row?.register_id;
}
