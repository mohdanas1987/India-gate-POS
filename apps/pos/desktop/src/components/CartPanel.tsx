import { computeLineTotal, formatMoney, formatWeight } from "../lib/pricing";

/**
 * Phase 9A cart rebuild. The gap analysis found the previous cart was a
 * plain <table> with no way to edit quantity except re-clicking the
 * product (+1 only), no remove-line control, and no per-line tax/weight
 * display. This adds: qty +/-, direct quantity entry (whole-unit
 * products only — a weighted line's "quantity" is grams chosen via
 * WeightEntryDialog, so it isn't hand-typed here), a remove button per
 * line, line selection (click a row to highlight it — the CTO plan's
 * "line selection" requirement; nothing yet acts on a selected line
 * beyond highlighting it, since per-line discount/void-before-checkout
 * are Phase 9B/9C scope, not 9A), and a Clear Cart action.
 *
 * Phase 9A correction gate (CTO review of d6bad7c, finding #4): every
 * line is now identified by its own `lineId` (a UUID minted once when
 * the line is added — see cartLineId.ts), not by `productId`. Two
 * separate weigh-ins of the same weighted product (Gouda 250g, Gouda
 * 500g) are two distinct lines with the same productId but different
 * lineIds, so removing/selecting/adjusting one can never affect the
 * other — a real bug in the original Phase 9A cart, since a React `key`
 * and every callback here were keyed on productId, which collided the
 * moment two lines shared a product. `productId` remains on the line as
 * the product REFERENCE (needed for checkout/pricing), it is just no
 * longer used as the line's identity.
 */
export interface CartLine {
  lineId: string;
  productId: number;
  name: string;
  quantity: number;
  unitPriceMinor: number;
  currency: string;
  isWeighted: boolean;
  taxRateBasisPoints: number | null;
  // Phase 9B: a flat amount (minor units) taken off this line. See
  // computeLineTotal in pricing.ts for how it's applied and clamped.
  discountMinor: number;
}

interface CartPanelProps {
  lines: CartLine[];
  selectedLineId: string | null;
  onSelectLine: (lineId: string | null) => void;
  onChangeQuantity: (lineId: string, quantity: number) => void;
  onChangeDiscount: (lineId: string, discountMinor: number) => void;
  onRemoveLine: (lineId: string) => void;
  onClearCart: () => void;
}

export function CartPanel({
  lines, selectedLineId, onSelectLine, onChangeQuantity, onChangeDiscount, onRemoveLine, onClearCart,
}: CartPanelProps) {
  if (lines.length === 0) {
    return <p className="text-sm text-gray-400 py-4 text-center">Cart is empty — search or scan a product to begin.</p>;
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-gray-500">
          <th className="pb-1">Product</th>
          <th className="pb-1">Qty</th>
          <th className="pb-1 text-right">Price</th>
          <th className="pb-1 text-right">Disc.</th>
          <th className="pb-1 text-right">Tax</th>
          <th className="pb-1 text-right">Line Total</th>
          <th className="pb-1" />
        </tr>
      </thead>
      <tbody>
        {lines.map((l) => {
          const { subtotalMinor, taxMinor, totalMinor } = computeLineTotal(
            { price_minor: l.unitPriceMinor, currency: l.currency, is_weighted: l.isWeighted, tax_rate_basis_points: l.taxRateBasisPoints },
            l.quantity,
            l.discountMinor
          );
          const isSelected = selectedLineId === l.lineId;
          return (
            <tr
              key={l.lineId}
              data-testid={`cart-line-${l.lineId}`}
              onClick={() => onSelectLine(isSelected ? null : l.lineId)}
              className={`cursor-pointer border-b last:border-b-0 ${isSelected ? "bg-yellow-50" : "hover:bg-gray-50"}`}
            >
              <td className="py-1">
                {l.name}
                {l.isWeighted && <span className="text-gray-400"> ({formatWeight(l.quantity)})</span>}
              </td>
              <td className="py-1">
                {l.isWeighted ? (
                  <span>{formatWeight(l.quantity)}</span>
                ) : (
                  <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      aria-label={`Decrease quantity of ${l.name}`}
                      data-testid={`decrease-qty-${l.lineId}`}
                      className="w-5 h-5 border rounded text-xs leading-none"
                      onClick={() => onChangeQuantity(l.lineId, l.quantity - 1)}
                    >
                      −
                    </button>
                    <input
                      type="number"
                      min={1}
                      className="w-12 border rounded px-1 text-center"
                      value={l.quantity}
                      onChange={(e) => {
                        const parsed = parseInt(e.target.value, 10);
                        if (Number.isFinite(parsed)) onChangeQuantity(l.lineId, parsed);
                      }}
                    />
                    <button
                      type="button"
                      aria-label={`Increase quantity of ${l.name}`}
                      data-testid={`increase-qty-${l.lineId}`}
                      className="w-5 h-5 border rounded text-xs leading-none"
                      onClick={() => onChangeQuantity(l.lineId, l.quantity + 1)}
                    >
                      +
                    </button>
                  </div>
                )}
              </td>
              <td className="py-1 text-right">{formatMoney(subtotalMinor, l.currency)}</td>
              <td className="py-1 text-right" onClick={(e) => e.stopPropagation()}>
                <input
                  type="number"
                  min={0}
                  step="0.01"
                  aria-label={`Discount for ${l.name}`}
                  data-testid={`discount-${l.lineId}`}
                  className="w-14 border rounded px-1 text-right text-xs"
                  value={(l.discountMinor / 100).toFixed(2)}
                  onChange={(e) => {
                    const parsed = Math.round(parseFloat(e.target.value || "0") * 100);
                    onChangeDiscount(l.lineId, Number.isFinite(parsed) && parsed >= 0 ? parsed : 0);
                  }}
                />
              </td>
              <td className="py-1 text-right text-gray-500">{formatMoney(taxMinor, l.currency)}</td>
              <td className="py-1 text-right font-medium">{formatMoney(totalMinor, l.currency)}</td>
              <td className="py-1 text-right">
                <button
                  type="button"
                  aria-label={`Remove ${l.name} from cart`}
                  data-testid={`remove-line-${l.lineId}`}
                  className="text-red-600 text-xs px-1"
                  onClick={(e) => {
                    e.stopPropagation();
                    onRemoveLine(l.lineId);
                  }}
                >
                  Remove
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
      <tfoot>
        <tr>
          <td colSpan={7} className="pt-2">
            <button type="button" className="text-xs text-gray-500 underline" onClick={onClearCart}>
              Clear cart
            </button>
          </td>
        </tr>
      </tfoot>
    </table>
  );
}
