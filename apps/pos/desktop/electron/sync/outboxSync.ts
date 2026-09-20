/**
 * Phase 8 — sync engine: drains outbox_events to the cloud API whenever
 * reachable, with exponential backoff (plan §43). The cashier flow never
 * calls this synchronously; it's a background loop.
 *
 * Phase 8.5 rework (CTO audit of 0cfd8ca, finding #18 — "the documentation
 * says exponential backoff, but the current engine effectively retries
 * every 5 seconds ... retry_count is incremented, but there is no actual
 * 2s/4s/8s/16s delay calculation"): that was a real, correctly-identified
 * gap. computeBackoffMs() below is the actual 2^n calculation with a cap
 * and jitter, and every failed row now gets a real next_attempt_at instead
 * of being retried on the very next fixed-interval tick regardless of how
 * many times it has already failed.
 */
import Database from "better-sqlite3";
import { randomUUID } from "crypto";
import { nextSequence } from "./schema";

/**
 * 2s, 4s, 8s, 16s, 32s, ... capped at 5 minutes, with +/-20% jitter so a
 * batch of events that all failed on the same tick don't all retry on
 * the exact same future tick and thunder-herd the API the moment
 * connectivity returns.
 */
export function computeBackoffMs(retryCount: number, capMs = 5 * 60 * 1000): number {
  const raw = Math.min(2000 * Math.pow(2, Math.max(0, retryCount - 1)), capMs);
  const jitterFactor = 0.8 + Math.random() * 0.4; // 0.8x - 1.2x
  return Math.round(raw * jitterFactor);
}

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
  next_attempt_at: string;
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
    // Finding #18 fix: only pick up rows whose next_attempt_at has
    // actually arrived. A row that just failed and got a 32-second
    // backoff must NOT be retried on the very next 5-second timer tick —
    // that was the entire bug: retry_count went up, but nothing ever
    // consulted it to decide WHEN to retry.
    const pending = this.db
      .prepare(
        `SELECT * FROM outbox_events
         WHERE synced_at IS NULL AND next_attempt_at <= datetime('now')
         ORDER BY sequence ASC LIMIT 25`
      )
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
        const newRetryCount = row.retry_count + 1;
        const delayMs = computeBackoffMs(newRetryCount);
        this.db
          .prepare(
            `UPDATE outbox_events
             SET retry_count = ?, last_error = ?, next_attempt_at = datetime('now', ?)
             WHERE event_id = ?`
          )
          .run(newRetryCount, String(err), `+${Math.round(delayMs / 1000)} seconds`, row.event_id);
      }
    }

    this.setState(anyFailure ? "SYNC_ERROR" : "PARTIALLY_CONNECTED");
  }
}
