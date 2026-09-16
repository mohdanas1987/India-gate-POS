/**
 * Phase 8 — sync engine: drains outbox_events to the cloud API whenever
 * reachable, with exponential backoff (plan §43). The cashier flow never
 * calls this synchronously; it's a background loop.
 */
import Database from "better-sqlite3";
import { randomUUID } from "crypto";
import { nextSequence } from "./schema";

export type ConnectivityState = "ONLINE" | "OFFLINE" | "SYNCING" | "SYNC_ERROR" | "PARTIALLY_CONNECTED";

export interface OutboxWriteArgs {
  aggregateType: string;
  aggregateId: string;
  eventType: string;
  payload: unknown;
}

export function enqueueOutboxEvent(db: Database.Database, args: OutboxWriteArgs): string {
  const eventId = randomUUID();
  const sequence = nextSequence(db);
  db.prepare(
    `INSERT INTO outbox_events (event_id, aggregate_type, aggregate_id, sequence, event_type, payload)
     VALUES (?, ?, ?, ?, ?, ?)`
  ).run(eventId, args.aggregateType, args.aggregateId, sequence, args.eventType, JSON.stringify(args.payload));
  return eventId;
}

interface OutboxRow {
  event_id: string;
  aggregate_type: string;
  aggregate_id: string;
  sequence: number;
  event_type: string;
  payload: string;
  retry_count: number;
}

export class OutboxSyncEngine {
  private state: ConnectivityState = "OFFLINE";
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private db: Database.Database,
    private apiBaseUrl: string,
    private deviceToken: () => string | null,
    private onStateChange: (state: ConnectivityState) => void
  ) {}

  start(intervalMs = 5000) {
    this.timer = setInterval(() => void this.tick(), intervalMs);
    void this.tick();
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
  }

  private setState(s: ConnectivityState) {
    this.state = s;
    this.onStateChange(s);
  }

  private async tick(): Promise<void> {
    const pending = this.db
      .prepare(`SELECT * FROM outbox_events WHERE synced_at IS NULL ORDER BY sequence ASC LIMIT 25`)
      .all() as OutboxRow[];

    if (pending.length === 0) {
      this.setState(this.state === "SYNC_ERROR" ? "SYNC_ERROR" : "ONLINE");
      return;
    }

    this.setState("SYNCING");
    let anyFailure = false;

    for (const row of pending) {
      try {
        const res = await fetch(`${this.apiBaseUrl}/api/v1/sync/events`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(this.deviceToken() ? { Authorization: `Bearer ${this.deviceToken()}` } : {}),
          },
          body: JSON.stringify({
            event_id: row.event_id,
            aggregate_type: row.aggregate_type,
            aggregate_id: row.aggregate_id,
            sequence: row.sequence,
            event_type: row.event_type,
            payload: JSON.parse(row.payload),
          }),
        });

        if (res.ok || res.status === 409) {
          // 409 = server already has this event_id (idempotent replay) — treat as synced, not an error.
          this.db
            .prepare(`UPDATE outbox_events SET synced_at = datetime('now') WHERE event_id = ?`)
            .run(row.event_id);
        } else {
          throw new Error(`HTTP ${res.status}`);
        }
      } catch (err) {
        anyFailure = true;
        const backoffRetry = row.retry_count + 1;
        this.db
          .prepare(`UPDATE outbox_events SET retry_count = ?, last_error = ? WHERE event_id = ?`)
          .run(backoffRetry, String(err), row.event_id);
      }
    }

    this.setState(anyFailure ? "SYNC_ERROR" : "PARTIALLY_CONNECTED");
  }
}
