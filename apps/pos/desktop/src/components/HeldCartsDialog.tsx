/**
 * Phase 9B — the RECALL half of suspended carts. Shown as a modal (F4)
 * listing every cart currently held on this store, most recent first.
 * Clicking one recalls it — see PosPage.tsx's handleRecallCart for what
 * happens next (the held cart only carries {product_id, quantity,
 * discount_minor}; the caller re-resolves full product detail via
 * GET /api/v1/products/{id}).
 */
export interface HeldCartSummary {
  id: number;
  label: string | null;
  held_at: string;
  lines: Array<{ product_id: number; quantity: number; discount_minor: number }>;
}

interface HeldCartsDialogProps {
  heldCarts: HeldCartSummary[];
  onRecall: (id: number) => void;
  onClose: () => void;
}

export function HeldCartsDialog({ heldCarts, onRecall, onClose }: HeldCartsDialogProps) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Held carts"
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <div className="bg-white rounded p-4 w-96 space-y-2 max-h-[80vh] overflow-y-auto">
        <div className="flex justify-between items-center">
          <h2 className="font-semibold">Held Carts</h2>
          <button type="button" className="text-sm text-gray-500" onClick={onClose}>
            ✕
          </button>
        </div>
        {heldCarts.length === 0 && <p className="text-sm text-gray-400 py-4 text-center">No carts on hold.</p>}
        {heldCarts.map((cart) => (
          <button
            key={cart.id}
            type="button"
            data-testid={`held-cart-${cart.id}`}
            className="block w-full text-left border rounded p-2 hover:bg-gray-50"
            onClick={() => onRecall(cart.id)}
          >
            <div className="text-sm font-medium">{cart.label || `Held cart #${cart.id}`}</div>
            <div className="text-xs text-gray-500">
              {cart.lines.length} line{cart.lines.length === 1 ? "" : "s"} — held {new Date(cart.held_at).toLocaleTimeString()}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
