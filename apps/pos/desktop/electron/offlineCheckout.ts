/**
 * Phase 8 (rebuild) — the actual "local-first checkout" the CTO audit
 * (commit c4bfb82, finding #5) found missing entirely: PosPage.tsx only
 * ever called `fetch(API_BASE + "/api/v1/orders")`, so a sale could not be
 * completed with no internet/API, directly contradicting the stated
 * requirement ("Basic POS checkout must work fully offline").
 *
 * This module runs in the MAIN process (better-sqlite3 is a native module
 * and cannot load in the sandboxed, nodeIntegration:false renderer — see
 * electron/main.ts for why the local DB is owned here, exposed to the
 * renderer only via the narrow IPC surface in preload.ts).
 *
 * Money math mirrors services/api/app/core/money.py's Money.percentage()
 * exactly (round(subtotal_minor * rate_basis_points / 10000)) so a
 * receipt printed offline shows the SAME total the server will compute
 * when the sale syncs — a mismatch there would be a real, confusing bug
 * (cashier hands over a receipt for €31.44, server later disagrees).
 */
import Database from "better-sqlite3";
import { randomUUID } from "crypto";
import { getLocalProduct, getLocalRegisterSession, LocalProductRow } from "./sync/schema";
import { enqueueOutboxEvent } from "./sync/outboxSync";

export class OfflineCheckoutError extends Error {}

export interface OfflineCartLine {
  productId: number;
  quantity: number;
}

export interface OfflineReceiptLine {
  productId: number;
  name: string;
  quantity: number;
  unitPriceMinor: number;
  lineSubtotalMinor: number;
  lineTaxMinor: number;
  lineTotalMinor: number;
}

export interface OfflineReceipt {
  localOrderId: string;
  eventId: string;
  lines: OfflineReceiptLine[];
  subtotalMinor: number;
  taxMinor: number;
  totalMinor: number;
  currency: string;
  createdAt: string;
}

const GRAMS_PER_KILO = 1000;

/**
 * Phase 9A finding (mirrors the identical fix in
 * services/api/app/services/checkout.py::compute_line_total — the two
 * must compute IDENTICALLY, per this file's own module docstring above,
 * or a receipt printed offline would show a different total than the
 * server later computes when the sale syncs): this used to always do
 * `price_minor * quantity`, ignoring `is_weighted`. For a weighted
 * product (price_minor is EUR per KILOGRAM, quantity is GRAMS — see
 * schema.ts's LocalProductRow / the server's OrderLine.quantity
 * docstring), that silently overcharged by 1000x. Weighted math now
 * matches the server exactly: `round(price_minor * grams / 1000)`, using
 * JS's own `Math.round()` (round-half-up on an exact .5, same as the
 * tax rounding immediately below) rather than truncating.
 */
function computeLineTotal(product: LocalProductRow, quantity: number): { subtotal: number; tax: number; total: number } {
  if (quantity <= 0) {
    throw new OfflineCheckoutError(`Product ${product.id} has non-positive quantity ${quantity}`);
  }
  const subtotal = product.is_weighted
    ? Math.round((product.price_minor * quantity) / GRAMS_PER_KILO)
    : product.price_minor * quantity;
  const tax = product.tax_rate_basis_points ? Math.round((subtotal * product.tax_rate_basis_points) / 10000) : 0;
  return { subtotal, tax, total: subtotal + tax };
}

/**
 * Runs entirely against the local SQLite database — zero network calls.
 * Writes the local order record AND enqueues the outbox event in the SAME
 * better-sqlite3 transaction, so a crash between the two can never happen
 * (either both are written, or neither is — matching the outbox pattern's
 * whole point per the plan).
 */
export function checkoutOffline(db: Database.Database, cart: OfflineCartLine[]): OfflineReceipt {
  if (cart.length === 0) {
    throw new OfflineCheckoutError("Cart is empty");
  }

  const session = getLocalRegisterSession(db);
  if (!session) {
    // This is the honestly-disclosed boundary of this rebuild: opening a
    // cashier session still requires connectivity (see
    // getLocalRegisterSession's caller in main.ts), so a device that has
    // NEVER been online long enough to open a session cannot ring up a
    // sale offline. Once a session has been opened online, every sale
    // for the rest of that session works with zero network calls.
    throw new OfflineCheckoutError(
      "No cached open cashier session — open a session while online at least once before going offline"
    );
  }

  const resolved = cart.map((line) => {
    const product = getLocalProduct(db, line.productId);
    if (!product) {
      throw new OfflineCheckoutError(
        `Product ${line.productId} is not in the local catalog — run a catalog sync while online`
      );
    }
    const { subtotal, tax, total } = computeLineTotal(product, line.quantity);
    return { product, quantity: line.quantity, subtotal, tax, total };
  });

  const subtotalMinor = resolved.reduce((sum, l) => sum + l.subtotal, 0);
  const taxMinor = resolved.reduce((sum, l) => sum + l.tax, 0);
  const totalMinor = subtotalMinor + taxMinor;
  const currency = resolved[0].product.currency;
  const localOrderId = randomUUID();
  const createdAt = new Date().toISOString();

  const receiptLines: OfflineReceiptLine[] = resolved.map((l) => ({
    productId: l.product.id,
    name: l.product.name,
    quantity: l.quantity,
    unitPriceMinor: l.product.price_minor,
    lineSubtotalMinor: l.subtotal,
    lineTaxMinor: l.tax,
    lineTotalMinor: l.total,
  }));

  const orderPayload = {
    register_id: session.register_id,
    payment_method: "CASH",
    lines: cart.map((l) => ({ product_id: l.productId, quantity: l.quantity })),
    // Carried for the local receipt/audit trail only — the server-side
    // sync handler (services/api/app/api/v1/sync.py) NEVER trusts these
    // client-computed totals; it recomputes from its own product/tax data
    // via the same create_pos_sale() the direct checkout API uses. This
    // is what stops a compromised or buggy offline client from writing an
    // arbitrary total to Postgres.
    client_computed_subtotal_minor: subtotalMinor,
    client_computed_tax_minor: taxMinor,
    client_computed_total_minor: totalMinor,
  };

  let eventId = "";
  const tx = db.transaction(() => {
    db.prepare(
      `INSERT INTO local_orders (id, status, total_minor, currency, created_at, payload)
       VALUES (?, 'PENDING_SYNC', ?, ?, ?, ?)`
    ).run(localOrderId, totalMinor, currency, createdAt, JSON.stringify({ receiptLines, session }));

    eventId = enqueueOutboxEvent(db, {
      aggregateType: "order",
      aggregateId: localOrderId,
      eventType: "order.created",
      payload: orderPayload,
    });
  });
  tx();

  return {
    localOrderId,
    eventId,
    lines: receiptLines,
    subtotalMinor,
    taxMinor,
    totalMinor,
    currency,
    createdAt,
  };
}
