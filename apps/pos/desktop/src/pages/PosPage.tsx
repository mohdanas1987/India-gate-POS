/**
 * Phase 9 — Main POS screen.
 *
 * Rebuilt during the CTO-audit remediation pass (commit c4bfb82 audit
 * findings #3/#5/#6/#8). Three real gaps are closed here, stated plainly:
 *
 * 1. There was no "open a cashier session" UI anywhere. Checkout only
 *    ever appeared to work in manual testing because a session had been
 *    opened once via a direct curl call against a persistent dev database
 *    and never closed — an undocumented, non-reproducible precondition.
 *    This screen now checks for an open session and prompts to open one
 *    if none exists, via the real `GET/POST /api/v1/cash/session`
 *    endpoints.
 *
 * 2. Checkout was cloud-dependent: it called `fetch(.../api/v1/orders)`
 *    directly, so a lost connection meant a cashier could not complete a
 *    sale at all — directly contradicting the "checkout must work fully
 *    offline" requirement. Inside Electron, checkout and product search
 *    now go through window.electronAPI, which runs entirely against the
 *    local SQLite catalog/outbox in the main process
 *    (electron/offlineCheckout.ts) — zero network calls on the hot path.
 *
 * 3. Running outside Electron (this is how the Playwright smoke test
 *    exercises the renderer, via `vite preview` in a plain browser tab)
 *    keeps the original direct-HTTP path, since window.electronAPI
 *    genuinely doesn't exist there. Both paths are real, not one faked —
 *    see PHASE-STATUS.md for exactly what's been verified for each.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ConnectivityBadge } from "../components/ConnectivityBadge";
import { authedFetch, isElectron } from "../api/authedFetch";

const DEFAULT_REGISTER_ID = 1; // single-register default until store/register selection UI exists (Phase 13/9)

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

interface OpenSessionDto {
  id: number;
  register_id: number;
  opening_cash_minor: number;
  is_open: boolean;
}

async function searchProductsOnline(q: string): Promise<ProductDto[]> {
  const result = await authedFetch<ProductDto[]>(`/api/v1/products?q=${encodeURIComponent(q)}`);
  if (!result.ok) throw new Error(`Product search failed: HTTP ${result.status}`);
  return result.body;
}

async function submitOrderOnline(lines: CartLine[]): Promise<OrderDto> {
  const result = await authedFetch<OrderDto & { detail?: string }>("/api/v1/orders", {
    method: "POST",
    body: {
      register_id: DEFAULT_REGISTER_ID,
      lines: lines.map((l) => ({ product_id: l.productId, quantity: l.quantity })),
    },
  });
  if (!result.ok) {
    throw new Error(typeof result.body?.detail === "string" ? result.body.detail : `Checkout failed: HTTP ${result.status}`);
  }
  return result.body;
}

async function getCurrentSessionOnline(): Promise<OpenSessionDto | null> {
  const result = await authedFetch<OpenSessionDto | null>(
    `/api/v1/cash/session/current?register_id=${DEFAULT_REGISTER_ID}`
  );
  if (!result.ok) throw new Error(`Could not check register status: HTTP ${result.status}`);
  return result.body;
}

async function openSessionOnline(openingCashMinor: number): Promise<OpenSessionDto> {
  const result = await authedFetch<OpenSessionDto & { detail?: string }>("/api/v1/cash/session/open", {
    method: "POST",
    body: { register_id: DEFAULT_REGISTER_ID, opening_cash_minor: openingCashMinor },
  });
  if (!result.ok) {
    throw new Error(typeof result.body?.detail === "string" ? result.body.detail : `Could not open register: HTTP ${result.status}`);
  }
  return result.body;
}

/**
 * Fires the cash-drawer + receipt-print IPC calls after a completed sale.
 * Never throws into the caller: a hardware/printer failure must not make
 * the sale itself look like it failed (the order already committed
 * server-side, or locally in the offline path) — it's surfaced as a
 * console warning instead.
 *
 * Sends STRUCTURED data, not HTML (CTO audit finding #18) — the actual
 * markup is generated in the trusted main process
 * (electron/hardware/electronPrinting.ts), not assembled here and handed
 * to the printer unrestricted.
 */
async function triggerHardwareForCompletedSale(orderLabel: string, lines: CartLine[], totalMinor: number) {
  if (!window.electronAPI) return; // renderer running outside Electron (e.g. smoke test)
  try {
    await window.electronAPI.drawCash();
  } catch (err) {
    console.warn("Cash drawer open failed (sale already recorded):", err);
  }
  try {
    await window.electronAPI.printReceipt({
      orderLabel: `India Gate POS — ${orderLabel}`,
      lines: lines.map((l) => ({ name: l.name, quantity: l.quantity, lineTotalMinor: l.unitPriceMinor * l.quantity })),
      totalMinor,
    });
  } catch (err) {
    console.warn("Receipt print failed (sale already recorded):", err);
  }
}

export function PosPage() {
  const [search, setSearch] = useState("");
  const [cart, setCart] = useState<CartLine[]>([]);
  const [lastReceipt, setLastReceipt] = useState<string | null>(null);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [hasOpenSession, setHasOpenSession] = useState(false);
  const [openingCash, setOpeningCash] = useState("");
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [localProducts, setLocalProducts] = useState<ProductDto[]>([]);
  const queryClient = useQueryClient();

  // On mount: figure out whether a session is already open, checking the
  // local cache first (works offline) and falling back to the server.
  useEffect(() => {
    let cancelled = false;
    async function checkSession() {
      try {
        if (isElectron()) {
          const cached = await window.electronAPI!.getCachedOpenSession();
          if (cached) {
            if (!cancelled) setHasOpenSession(true);
            return;
          }
        }
        const online = await getCurrentSessionOnline();
        if (!cancelled) setHasOpenSession(!!online?.is_open);
      } catch (err) {
        // No network and no cached session — genuinely cannot proceed,
        // and the UI says so rather than pretending a session exists.
        if (!cancelled) setSessionError((err as Error).message);
      } finally {
        if (!cancelled) setSessionChecked(true);
      }
    }
    void checkSession();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleOpenRegister(e: React.FormEvent) {
    e.preventDefault();
    setSessionError(null);
    try {
      const amountMinor = Math.round(parseFloat(openingCash || "0") * 100);
      const session = await openSessionOnline(amountMinor);
      if (isElectron()) {
        // Cache the opened session locally so checkout can validate it
        // with zero network calls even if connectivity drops right after.
        const meRes = await authedFetch<{ user_id: number; tenant_id: number; store_id: number | null }>(
          "/api/v1/auth/me"
        );
        const me = meRes.body;
        if (me.store_id === null) {
          // Can't happen in practice: the server now refuses to open a
          // session at all for a user with no store assigned (see
          // app/api/v1/cash.py) — this is just satisfying the type
          // checker's honest "store_id could be null" signature.
          throw new Error("Logged-in user has no store assigned — cannot cache session");
        }
        await window.electronAPI!.cacheOpenSession({
          session_id: session.id,
          register_id: session.register_id,
          store_id: me.store_id,
          tenant_id: me.tenant_id,
          cashier_user_id: me.user_id,
          opened_at: new Date().toISOString(),
        });
        await window.electronAPI!.syncCatalog().catch((err) => console.warn("Catalog sync failed:", err));
      }
      setHasOpenSession(true);
    } catch (err) {
      setSessionError((err as Error).message);
    }
  }

  const { data: onlineProducts } = useQuery({
    queryKey: ["products", search],
    queryFn: () => searchProductsOnline(search),
    enabled: search.length > 0 && !isElectron(),
  });

  useEffect(() => {
    if (!isElectron() || search.length === 0) {
      setLocalProducts([]);
      return;
    }
    let cancelled = false;
    window.electronAPI!.searchLocalProducts(search).then((rows) => {
      if (cancelled) return;
      setLocalProducts(
        rows.map((r) => ({ id: r.id, name: r.name, sku: r.sku, price_minor: r.price_minor, currency: r.currency }))
      );
    });
    return () => {
      cancelled = true;
    };
  }, [search]);

  const products = isElectron() ? localProducts : onlineProducts ?? [];

  const checkoutMutation = useMutation({
    mutationFn: async () => {
      if (isElectron()) {
        const result = await window.electronAPI!.checkoutOffline(
          cart.map((l) => ({ productId: l.productId, quantity: l.quantity }))
        );
        if (!result.ok) throw new Error(result.error);
        return { label: `Order (offline, syncing) #${result.receipt.localOrderId.slice(0, 8)}`, totalMinor: result.receipt.totalMinor };
      }
      const order = await submitOrderOnline(cart);
      return { label: `Order #${order.id}`, totalMinor: order.total_minor };
    },
    onSuccess: ({ label, totalMinor }) => {
      const soldLines = cart;
      setCart([]);
      setLastReceipt(`${label} — total €${(totalMinor / 100).toFixed(2)}`);
      queryClient.invalidateQueries({ queryKey: ["products"] });
      void triggerHardwareForCompletedSale(label, soldLines, totalMinor);
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

  if (!sessionChecked) {
    return <div className="p-6">Checking register status…</div>;
  }

  if (!hasOpenSession) {
    return (
      <div className="h-screen flex items-center justify-center">
        <form onSubmit={handleOpenRegister} className="w-80 space-y-3 border rounded p-6">
          <h1 className="text-lg font-semibold">Open Register</h1>
          <p className="text-sm text-gray-500">
            No cashier session is open on this register yet. Enter the opening cash amount to start one.
          </p>
          <input
            className="w-full border rounded px-2 py-1"
            type="number"
            step="0.01"
            placeholder="Opening cash (€)"
            value={openingCash}
            onChange={(e) => setOpeningCash(e.target.value)}
            required
          />
          {sessionError && <p className="text-sm text-red-600">{sessionError}</p>}
          <button type="submit" className="w-full bg-black text-white rounded py-2">
            Open Register
          </button>
        </form>
      </div>
    );
  }

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
            {products.map((p) => (
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
          <span>Subtotal (tax computed {isElectron() ? "locally, offline-first" : "server-side at checkout"})</span>
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
