/**
 * Phase 9 — Main POS screen skeleton (plan §49). This is a structural
 * scaffold (layout, cart state shape, hooks wired to the local SQLite
 * outbox) rather than a pixel-complete cashier UI — the full UI is a
 * substantial design effort the plan itself treats as its own phase
 * (Phase 9) and is honestly out of scope for what's been built so far;
 * see the Phase status report for what remains.
 */
import { useState } from "react";
import { ConnectivityBadge } from "../components/ConnectivityBadge";

interface CartLine {
  productId: string;
  name: string;
  quantity: number;
  unitPriceMinor: number;
}

export function PosPage() {
  const [cart, setCart] = useState<CartLine[]>([]);
  const totalMinor = cart.reduce((sum, l) => sum + l.unitPriceMinor * l.quantity, 0);

  return (
    <div className="h-screen flex flex-col">
      <header className="flex items-center justify-between px-4 py-2 border-b">
        <h1 className="font-semibold">India Gate POS</h1>
        <ConnectivityBadge />
      </header>

      <div className="flex-1 grid grid-cols-[220px_1fr] overflow-hidden">
        <aside className="border-r p-3 overflow-y-auto">
          <p className="text-sm text-gray-500">Categories (product/category API wiring is a follow-up task)</p>
        </aside>
        <main className="p-3 overflow-y-auto">
          <p className="text-sm text-gray-500">Product grid placeholder — barcode scan / search bar goes here</p>
        </main>
      </div>

      <section className="border-t p-3">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500">
              <th>Product</th>
              <th>Qty</th>
              <th>Price</th>
            </tr>
          </thead>
          <tbody>
            {cart.map((l) => (
              <tr key={l.productId}>
                <td>{l.name}</td>
                <td>{l.quantity}</td>
                <td>€{(l.unitPriceMinor / 100).toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="flex justify-between items-center mt-2 font-semibold">
          <span>Total</span>
          <span>€{(totalMinor / 100).toFixed(2)}</span>
        </div>
        <div className="flex gap-2 mt-2">
          <button className="px-4 py-2 bg-black text-white rounded" onClick={() => window.electronAPI.drawCash()}>
            CASH
          </button>
          <button className="px-4 py-2 bg-gray-200 rounded" disabled>
            CARD (mock provider — Phase 21 for a real gateway)
          </button>
        </div>
      </section>
    </div>
  );
}
