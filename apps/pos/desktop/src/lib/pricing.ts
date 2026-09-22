/**
 * Phase 9A — shared, DISPLAY-ONLY pricing math for the renderer.
 *
 * This mirrors services/api/app/services/checkout.py::compute_line_total
 * and electron/offlineCheckout.ts::computeLineTotal exactly (weighted
 * products: price_minor is EUR per KILOGRAM, quantity is GRAMS; the line
 * subtotal is round(price_minor * grams / 1000)) so the cart the cashier
 * sees before tapping "Complete Sale" already shows the real total —
 * before this fix, the cart table did `unitPriceMinor * quantity` for
 * every line, which is wrong for a weighted item (250g would have shown
 * as costing 250x the per-kilo price).
 *
 * SECURITY NOTE (CTO plan §37 — "the frontend is NOT trusted"): this
 * function exists only to render an accurate preview. It is never sent
 * to the server as the authoritative total — the backend independently
 * recomputes every sale from its own product/tax data
 * (create_pos_sale/compute_line_total), both for the direct online route
 * and for an offline sale syncing in through /api/v1/sync/events. If this
 * file and the server ever disagreed, the server's number is what
 * actually gets charged; this only affects what the cashier sees before
 * that happens.
 */

export const GRAMS_PER_KILO = 1000;

export interface PricedProduct {
  price_minor: number;
  currency: string;
  is_weighted: boolean;
  tax_rate_basis_points: number | null;
}

export interface LineTotal {
  subtotalMinor: number;
  taxMinor: number;
  totalMinor: number;
}

export function computeLineTotal(product: PricedProduct, quantity: number): LineTotal {
  if (quantity <= 0) {
    return { subtotalMinor: 0, taxMinor: 0, totalMinor: 0 };
  }
  const subtotalMinor = product.is_weighted
    ? Math.round((product.price_minor * quantity) / GRAMS_PER_KILO)
    : product.price_minor * quantity;
  const taxMinor = product.tax_rate_basis_points
    ? Math.round((subtotalMinor * product.tax_rate_basis_points) / 10000)
    : 0;
  return { subtotalMinor, taxMinor, totalMinor: subtotalMinor + taxMinor };
}

export function formatMoney(minor: number, currency = "EUR"): string {
  const symbol = currency === "EUR" ? "€" : `${currency} `;
  return `${symbol}${(minor / 100).toFixed(2)}`;
}

/** Preset weight steps offered in the weighted-quantity picker (grams). */
export const WEIGHT_PRESETS_GRAMS = [100, 250, 500, 1000, 2000, 5000, 10000];

export function formatWeight(grams: number): string {
  if (grams >= GRAMS_PER_KILO) {
    const kg = grams / GRAMS_PER_KILO;
    // Show up to 3 decimals but trim trailing zeros (1.000 -> 1, 1.500 -> 1.5)
    return `${parseFloat(kg.toFixed(3))} kg`;
  }
  return `${grams} g`;
}
