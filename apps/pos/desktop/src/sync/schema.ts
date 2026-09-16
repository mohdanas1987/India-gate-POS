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
  `);

  return db;
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
