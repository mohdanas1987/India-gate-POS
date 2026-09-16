/**
 * Phase 9 — Main POS screen, now wired to the real backend built in this
 * session (app/api/v1/products.py, app/api/v1/orders.py), and now also
 * wired to the real hardware IPC (electron/hardware) on a completed sale:
 * cash drawer opens and a receipt is sent to the printer via the ported,
 * hardened silent-print path (see Phase 10). Still missing vs. the plan's
 * full §49 design: category sidebar filtering, barcode scanner input
 * handling, weighted-product quantity entry, and split payments. This is
 * a working, honest slice, not the finished screen.
 *
 * NOTE: window.electronAPI is only present when running inside the
 * Electron shell (main.ts calls contextBridge in preload.ts). The
 * Playwright smoke test runs the renderer alone via `vite preview` in a
 * plain browser tab, so window.electronAPI is undefined there — the calls
 * below are guarded and are a no-op (not a crash) outside Electron.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ConnectivityBadge } from "../components/ConnectivityBadge";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8100";
const DEFAULT_REGISTER_ID = 1; // single-register default until store/register selection UI exists

function authHeaders() {
  const token = localStorage.getItem("igpos_dev_token") ?? "";
  return { Authorization: `Bearer ${token}` };
}

interface ProductDto {
  id: number;
  name: string;
  sku: string | null;
  price_minor: number;
  currency: string;
}

interface CartLine {
  productId: number;
  name: string;
  quantity: number;
  unitPriceMinor: number;
}

interface OrderDto {
  id: number;
  total_minor: number;
}

// `window.electronAPI` is declared (non-optional) in src/global.d.ts because
// it's always present inside the Electron shell. It is genuinely absent when
// the renderer runs standalone (e.g. `vite preview` under the Playwright
// smoke test), so every use below is guarded at runtime despite the type.

function buildReceiptHtml(order: OrderDto, lines: CartLine[]): string {
  const rows = lines
    .map(
      (l) =>
        `<tr><td>${l.name}</td><td>${l.quantity}</td><td>€${((l.unitPriceMinor * l.quantity) / 100).toFixed(2)}</td></tr>`
    )
    .join("");
  return `<html><body>
    <h3>India Gate POS — Order #${order.id}</h3>
    <table>${rows}</table>
    <p><strong>Total: €${(order.total_minor / 100).toFixed(2)}</strong></p>
  </body></html>`;
}

/**
 * Fires the cash-drawer + receipt-print IPC calls after a completed sale.
 * Never throws into the caller: a hardware/printer failure must not make
 * the sale itself look like it failed (the order already committed
 * server-side) — it's surfaced as a console warning instead, matching the
 * plan's principle that a completed sale record is the source of truth,
 * not the physical receipt.
 */
async function triggerHardwareForCompletedSale(order: OrderDto, lines: CartLine[]) {
  if (!window.electronAPI) return; // renderer running outside Electron (e.g. smoke test)
  try {
    await window.electronAPI.drawCash();
  } catch (err) {
    console.warn("Cash drawer open failed (sale already recorded):", err);
  }
  try {
    await window.electronAPI.printReceipt(buildReceiptHtml(order, lines));
  } catch (err) {
    console.warn("Receipt print failed (sale already recorded):", err);
  }
}

async function searchProducts(q: string): Promise<ProductDto[]> {
  const res = await fetch(`${API_BASE}/api/v1/products?q=${encodeURIComponent(q)}`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`Product search failed: HTTP ${res.status}`);
  return res.json();
}

async function submitOrder(lines: CartLine[]): Promise<OrderDto> {
  const res = await fetch(`${API_BASE}/api/v1/orders`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      register_id: DEFAULT_REGISTER_ID,
      lines: lines.map((l) => ({ product_id: l.productId, quantity: l.quantity })),
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `Checkout failed: HTTP ${res.status}`);
  }
  return res.json();
}

export function PosPage() {
  const [search, setSearch] = useState("");
  const [cart, setCart] = useState<CartLine[]>([]);
  const [lastReceipt, setLastReceipt] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: products } = useQuery({
    queryKey: ["products", search],
    queryFn: () => searchProducts(search),
    enabled: search.length > 0,
  });

  const checkoutMutation = useMutation({
    mutationFn: () => submitOrder(cart),
    onSuccess: (order) => {
      const soldLines = cart;
      setCart([]);
      setLastReceipt(`Order #${order.id} — total €${(order.total_minor / 100).toFixed(2)}`);
      queryClient.invalidateQueries({ queryKey: ["products"] });
      void triggerHardwareForCompletedSale(order, soldLines);
    },
  });

  function addToCart(p: ProductDto) {
    setCart((prev) => {
      const existing = prev.find((l) => l.productId === p.id);
      if (existing) {
        return prev.map((l) => (l.productId === p.id ? { ...l, quantity: l.quantity + 1 } : l));
      }
      return [...prev, { productId: p.id, name: p.name, quantity: 1, unitPriceMinor: p.price_minor }];
    });
  }

  const totalMinor = cart.reduce((sum, l) => sum + l.unitPriceMinor * l.quantity, 0);

  return (
    <div className="h-screen flex flex-col">
      <header className="flex items-center justify-between px-4 py-2 border-b">
        <h1 className="font-semibold">India Gate POS</h1>
        <ConnectivityBadge />
      </header>

      <div className="flex-1 grid grid-cols-[1fr] overflow-hidden">
        <main className="p-3 overflow-y-auto">
          <input
            className="w-full border rounded px-2 py-1 mb-2"
            placeholder="Search product name or SKU (barcode-scan wedge input is a follow-up task)…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="grid grid-cols-3 gap-2">
            {(products ?? []).map((p) => (
              <button
                key={p.id}
                onClick={() => addToCart(p)}
                className="border rounded p-2 text-left hover:bg-gray-50"
              >
                <div className="text-sm font-medium">{p.name}</div>
                <div className="text-xs text-gray-500">€{(p.price_minor / 100).toFixed(2)}</div>
              </button>
            ))}
          </div>
        </main>
      </div>

      <section className="border-t p-3">
        {lastReceipt && <p className="text-sm text-green-700 mb-2">{lastReceipt}</p>}
        {checkoutMutation.isError && (
          <p className="text-sm text-red-600 mb-2">{(checkoutMutation.error as Error).message}</p>
        )}
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
                <td>€{((l.unitPriceMinor * l.quantity) / 100).toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="flex justify-between items-center mt-2 font-semibold">
          <span>Subtotal (tax computed server-side at checkout)</span>
          <span>€{(totalMinor / 100).toFixed(2)}</span>
        </div>
        <div className="flex gap-2 mt-2">
          <button
            className="px-4 py-2 bg-black text-white rounded disabled:opacity-50"
            disabled={cart.length === 0 || checkoutMutation.isPending}
            onClick={() => checkoutMutation.mutate()}
          >
            {checkoutMutation.isPending ? "Processing…" : "CASH — Complete Sale"}
          </button>
          <button className="px-4 py-2 bg-gray-200 rounded" disabled>
            CARD (mock provider — Phase 21 for a real gateway)
          </button>
        </div>
      </section>
    </div>
  );
}
