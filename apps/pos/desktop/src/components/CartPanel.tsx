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
 */
export interface CartLine {
  productId: number;
  name: string;
  quantity: number;
  unitPriceMinor: number;
  currency: string;
  isWeighted: boolean;
  taxRateBasisPoints: number | null;
}

interface CartPanelProps {
  lines: CartLine[];
  selectedProductId: number | null;
  onSelectLine: (productId: number | null) => void;
  onChangeQuantity: (productId: number, quantity: number) => void;
  onRemoveLine: (productId: number) => void;
  onClearCart: () => void;
}

export function CartPanel({ lines, selectedProductId, onSelectLine, onChangeQuantity, onRemoveLine, onClearCart }: CartPanelProps) {
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
          <th className="pb-1 text-right">Tax</th>
          <th className="pb-1 text-right">Line Total</th>
          <th className="pb-1" />
        </tr>
      </thead>
      <tbody>
        {lines.map((l) => {
          const { subtotalMinor, taxMinor, totalMinor } = computeLineTotal(
            { price_minor: l.unitPriceMinor, currency: l.currency, is_weighted: l.isWeighted, tax_rate_basis_points: l.taxRateBasisPoints },
            l.quantity
          );
          const isSelected = selectedProductId === l.productId;
          return (
            <tr
              key={l.productId}
              onClick={() => onSelectLine(isSelected ? null : l.productId)}
              className={`cursor-pointer border-b last:border-b-0 ${isSelected ? "bg-yellow-50" : "hover:bg-gray-50"}`}
            >
              <td className="py-1">{l.name}</td>
              <td className="py-1">
                {l.isWeighted ? (
                  <span>{formatWeight(l.quantity)}</span>
                ) : (
                  <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      aria-label={`Decrease quantity of ${l.name}`}
                      className="w-5 h-5 border rounded text-xs leading-none"
                      onClick={() => onChangeQuantity(l.productId, l.quantity - 1)}
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
                        if (Number.isFinite(parsed)) onChangeQuantity(l.productId, parsed);
                      }}
                    />
                    <button
                      type="button"
                      aria-label={`Increase quantity of ${l.name}`}
                      className="w-5 h-5 border rounded text-xs leading-none"
                      onClick={() => onChangeQuantity(l.productId, l.quantity + 1)}
                    >
                      +
                    </button>
                  </div>
                )}
              </td>
              <td className="py-1 text-right">{formatMoney(subtotalMinor, l.currency)}</td>
              <td className="py-1 text-right text-gray-500">{formatMoney(taxMinor, l.currency)}</td>
              <td className="py-1 text-right font-medium">{formatMoney(totalMinor, l.currency)}</td>
              <td className="py-1 text-right">
                <button
                  type="button"
                  aria-label={`Remove ${l.name} from cart`}
                  className="text-red-600 text-xs px-1"
                  onClick={(e) => {
                    e.stopPropagation();
                    onRemoveLine(l.productId);
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
          <td colSpan={6} className="pt-2">
            <button type="button" className="text-xs text-gray-500 underline" onClick={onClearCart}>
              Clear cart
            </button>
          </td>
        </tr>
      </tfoot>
    </table>
  );
}
