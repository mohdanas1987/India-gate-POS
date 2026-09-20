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
      last_error TEXT
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
      tax_rate_basis_points INTEGER,   -- NULL = no tax, matches server's tax_id NULL case
      barcodes TEXT NOT NULL DEFAULT '[]',  -- JSON array of barcode strings
      synced_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_local_products_name ON local_products(name);
    CREATE INDEX IF NOT EXISTS idx_local_products_sku ON local_products(sku);

    -- Holds at most one row: the currently-open cashier session, cached
    -- from the last successful (online) /api/v1/cash/session/open call.
    -- This is what lets checkout validate "is there an open session"
    -- without a network round-trip. Opening/closing a session itself still
    -- requires connectivity (disclosed limitation — see PHASE-STATUS.md).
    CREATE TABLE IF NOT EXISTS local_register_session (
      id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton row
      session_id INTEGER NOT NULL,
      register_id INTEGER NOT NULL,
      store_id INTEGER NOT NULL,
      tenant_id INTEGER NOT NULL,
      cashier_user_id INTEGER NOT NULL,
      opened_at TEXT NOT NULL
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
  `);

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
      `INSERT INTO local_products (id, name, sku, price_minor, currency, unit, is_weighted, tax_rate_basis_points, barcodes)
       VALUES (@id, @name, @sku, @price_minor, @currency, @unit, @is_weighted, @tax_rate_basis_points, @barcodes)`
    );
    for (const row of rows) insert.run(row);
  });
  tx(products);
}

export function searchLocalProducts(db: Database.Database, query: string, limit = 50): LocalProductRow[] {
  const like = `%${query}%`;
  return db
    .prepare(
      `SELECT * FROM local_products WHERE name LIKE ? OR sku LIKE ? OR barcodes LIKE ? ORDER BY name LIMIT ?`
    )
    .all(like, like, like, limit) as LocalProductRow[];
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
}

export function saveLocalRegisterSession(db: Database.Database, s: LocalRegisterSession): void {
  db.prepare(
    `INSERT INTO local_register_session (id, session_id, register_id, store_id, tenant_id, cashier_user_id, opened_at)
     VALUES (1, @session_id, @register_id, @store_id, @tenant_id, @cashier_user_id, @opened_at)
     ON CONFLICT(id) DO UPDATE SET session_id=excluded.session_id, register_id=excluded.register_id,
       store_id=excluded.store_id, tenant_id=excluded.tenant_id, cashier_user_id=excluded.cashier_user_id,
       opened_at=excluded.opened_at`
  ).run(s);
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
