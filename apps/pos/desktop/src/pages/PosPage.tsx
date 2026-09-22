/**
 * Phase 9A — POS Transaction UX Foundation.
 *
 * Rebuilt from the Phase 9 "prototype" screen (single-column, no category
 * nav, +1-only cart, no weighted-product support, no keyboard nav — see
 * the Phase 9A gap analysis in PHASE-STATUS.md for the full before/after)
 * to meet the CTO plan's Phase 9A acceptance criteria: category
 * navigation + real search (name/SKU/barcode), a proper cart (qty
 * edit/remove/line-select/clear), weighted-product entry, and basic
 * keyboard/scanner UX — all working identically online and offline.
 *
 * Retains, unchanged, the two real fixes from the prior remediation pass:
 * 1. Session-gating: checkout is blocked behind a real "Open Register"
 *    screen backed by GET/POST /api/v1/cash/session (+ Phase 22.1's
 *    offline shift-start fallback).
 * 2. Environment split: inside Electron, search/checkout run entirely
 *    against the local SQLite catalog/outbox (electron/offlineCheckout.ts)
 *    with zero network calls; outside it (the Playwright smoke test's
 *    plain-browser renderer), the original direct-HTTP path is used,
 *    since window.electronAPI genuinely doesn't exist there.
 *
 * SECURITY (CTO plan §37): every price/tax/total shown here
 * (src/lib/pricing.ts) is a DISPLAY-ONLY preview. The backend
 * independently recomputes and is the only source of the amount actually
 * charged — see compute_line_total's docstring in checkout.py.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ConnectivityBadge } from "../components/ConnectivityBadge";
import { CategorySidebar, CategoryDto } from "../components/CategorySidebar";
import { CartPanel, CartLine } from "../components/CartPanel";
import { WeightEntryDialog } from "../components/WeightEntryDialog";
import { computeLineTotal, formatMoney } from "../lib/pricing";
import { generateCartLineId } from "../lib/cartLineId";
import { authedFetch, isElectron } from "../api/authedFetch";

const DEFAULT_REGISTER_ID = 1; // single-register default until store/register selection UI exists (Phase 13/9)

interface ProductDto {
  id: number;
  name: string;
  sku: string | null;
  price_minor: number;
  currency: string;
  unit: string;
  is_weighted: boolean;
  category_id: number | null;
  tax_rate_basis_points: number | null;
  barcodes: string[];
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

async function searchProductsOnline(q: string, categoryId: number | null): Promise<ProductDto[]> {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (categoryId !== null) params.set("category_id", String(categoryId));
  const result = await authedFetch<ProductDto[]>(`/api/v1/products?${params.toString()}`);
  if (!result.ok) throw new Error(`Product search failed: HTTP ${result.status}`);
  return result.body;
}

async function listCategoriesOnline(): Promise<CategoryDto[]> {
  const result = await authedFetch<CategoryDto[]>("/api/v1/categories");
  if (!result.ok) throw new Error(`Could not load categories: HTTP ${result.status}`);
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
      lines: lines.map((l) => {
        const { totalMinor: lineTotal } = computeLineTotal(
          { price_minor: l.unitPriceMinor, currency: l.currency, is_weighted: l.isWeighted, tax_rate_basis_points: l.taxRateBasisPoints },
          l.quantity
        );
        return { name: l.name, quantity: l.quantity, lineTotalMinor: lineTotal };
      }),
      totalMinor,
    });
  } catch (err) {
    console.warn("Receipt print failed (sale already recorded):", err);
  }
}

export function PosPage() {
  const [search, setSearch] = useState("");
  const [selectedCategoryId, setSelectedCategoryId] = useState<number | null>(null);
  const [cart, setCart] = useState<CartLine[]>([]);
  const [selectedLineId, setSelectedLineId] = useState<string | null>(null);
  const [lastReceipt, setLastReceipt] = useState<string | null>(null);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [hasOpenSession, setHasOpenSession] = useState(false);
  const [openingCash, setOpeningCash] = useState("");
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [localProducts, setLocalProducts] = useState<ProductDto[]>([]);
  const [localCategories, setLocalCategories] = useState<CategoryDto[]>([]);
  const [weightDialogProduct, setWeightDialogProduct] = useState<ProductDto | null>(null);
  const [focusedProductIndex, setFocusedProductIndex] = useState(0);
  const searchInputRef = useRef<HTMLInputElement>(null);
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

  // Phase 9A: keyboard shortcut bar. F1 focuses search (the mockup's own
  // "F1 Search" hotkey). F3 Hold / F4 Recall are shown, disabled, in the
  // hotkey bar below — they belong to Phase 9B's suspended-cart feature
  // and are deliberately NOT wired to fake functionality here.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "F1") {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  async function handleOpenRegister(e: React.FormEvent) {
    e.preventDefault();
    setSessionError(null);
    const amountMinor = Math.round(parseFloat(openingCash || "0") * 100);
    try {
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
    } catch (onlineErr) {
      // Phase 22.1 (CTO gate: offline shift-start). The online attempt
      // failed — could be no network, could be a genuine rejection
      // (wrong register, no permission, etc). Only worth trying the
      // offline path inside Electron; outside it there is no local
      // device database to check against at all.
      if (!isElectron()) {
        setSessionError((onlineErr as Error).message);
        return;
      }
      const offline = await window.electronAPI!.openShiftOffline(DEFAULT_REGISTER_ID, amountMinor);
      if (offline.ok) {
        setHasOpenSession(true);
      } else {
        // Report both: the online failure is often "network unreachable"
        // (uninformative), while the offline failure explains exactly
        // why offline couldn't take over either (e.g. "sign in online at
        // least once first") — a cashier troubleshooting this needs the
        // second message, not just the first.
        setSessionError(`${(onlineErr as Error).message}. Offline fallback also failed: ${offline.error}`);
      }
    }
  }

  // --- Categories (online query hook + offline effect, same split
  // pattern the rest of this file already uses for products) ---

  const { data: onlineCategories } = useQuery({
    queryKey: ["categories"],
    queryFn: listCategoriesOnline,
    enabled: !isElectron() && hasOpenSession,
  });

  useEffect(() => {
    if (!isElectron() || !hasOpenSession) return;
    let cancelled = false;
    window.electronAPI!.listLocalCategories().then((rows) => {
      if (!cancelled) setLocalCategories(rows);
    });
    return () => {
      cancelled = true;
    };
  }, [hasOpenSession]);

  const categories = isElectron() ? localCategories : onlineCategories ?? [];

  // --- Products ---

  const { data: onlineProducts } = useQuery({
    queryKey: ["products", search, selectedCategoryId],
    queryFn: () => searchProductsOnline(search, selectedCategoryId),
    enabled: !isElectron() && hasOpenSession && (search.length > 0 || selectedCategoryId !== null),
  });

  useEffect(() => {
    if (!isElectron() || !hasOpenSession) {
      setLocalProducts([]);
      return;
    }
    if (search.length === 0 && selectedCategoryId === null) {
      setLocalProducts([]);
      return;
    }
    let cancelled = false;
    window.electronAPI!.searchLocalProducts(search || null, selectedCategoryId).then((rows) => {
      if (cancelled) return;
      setLocalProducts(
        rows.map((r) => ({
          id: r.id,
          name: r.name,
          sku: r.sku,
          price_minor: r.price_minor,
          currency: r.currency,
          unit: r.unit,
          is_weighted: !!r.is_weighted,
          category_id: r.category_id,
          tax_rate_basis_points: r.tax_rate_basis_points,
          barcodes: JSON.parse(r.barcodes || "[]") as string[],
        }))
      );
    });
    return () => {
      cancelled = true;
    };
  }, [search, selectedCategoryId, hasOpenSession]);

  const products = isElectron() ? localProducts : onlineProducts ?? [];

  useEffect(() => {
    setFocusedProductIndex(0);
  }, [products]);

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
      setSelectedLineId(null);
      setLastReceipt(`${label} — total ${formatMoney(totalMinor)}`);
      queryClient.invalidateQueries({ queryKey: ["products"] });
      void triggerHardwareForCompletedSale(label, soldLines, totalMinor);
    },
  });

  function addToCart(p: ProductDto, quantity = 1) {
    setCart((prev) => {
      const existing = prev.find((l) => l.productId === p.id && !p.is_weighted);
      if (existing) {
        // Weighted lines are never merged — each weigh-in is its own
        // line (a cashier re-weighing the same product is a second sale
        // of it, not automatically additive), matching how a real scale
        // workflow behaves. Whole-unit products still merge quantities,
        // keeping the SAME lineId (it's the same cart line getting
        // bigger, not a new one).
        return prev.map((l) => (l.lineId === existing.lineId ? { ...l, quantity: l.quantity + quantity } : l));
      }
      return [
        ...prev,
        {
          // Phase 9A correction gate (CTO review of d6bad7c, finding
          // #4): identity is the lineId, minted fresh here — never
          // productId, which two lines can legitimately share (two
          // separate weigh-ins of the same weighted product).
          lineId: generateCartLineId(),
          productId: p.id,
          name: p.name,
          quantity,
          unitPriceMinor: p.price_minor,
          currency: p.currency,
          isWeighted: p.is_weighted,
          taxRateBasisPoints: p.tax_rate_basis_points,
        },
      ];
    });
  }

  function handleProductActivate(p: ProductDto) {
    if (p.is_weighted) {
      setWeightDialogProduct(p);
    } else {
      addToCart(p);
    }
  }

  function changeCartQuantity(lineId: string, quantity: number) {
    setCart((prev) => {
      if (quantity <= 0) return prev.filter((l) => l.lineId !== lineId);
      return prev.map((l) => (l.lineId === lineId ? { ...l, quantity } : l));
    });
  }

  function removeCartLine(lineId: string) {
    setCart((prev) => prev.filter((l) => l.lineId !== lineId));
    setSelectedLineId((prev) => (prev === lineId ? null : prev));
  }

  function clearCart() {
    setCart([]);
    setSelectedLineId(null);
  }

  // Phase 9A "scanner input" support: a barcode scanner is, functionally,
  // a very fast keyboard typist that ends with Enter. Rather than build a
  // timing-based heuristic (fragile, and untestable without real
  // hardware — the CTO plan's own honesty rule against inventing
  // unverifiable behavior applies here), this takes the simpler,
  // deterministic approach: on Enter, if the current search text is an
  // EXACT match against exactly one visible product's SKU or a barcode,
  // that product is added immediately and the search clears — the same
  // outcome a scanner-then-Enter produces, and it also works for a
  // cashier who types a SKU by hand and presses Enter.
  function handleSearchKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      setSearch("");
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setFocusedProductIndex((i) => Math.min(i + 1, Math.max(products.length - 1, 0)));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setFocusedProductIndex((i) => Math.max(i - 1, 0));
      return;
    }
    if (e.key !== "Enter") return;
    e.preventDefault();
    const query = search.trim();
    const exactMatches = query
      ? products.filter((p) => p.sku === query || p.barcodes.includes(query))
      : [];
    if (exactMatches.length === 1) {
      handleProductActivate(exactMatches[0]);
      setSearch("");
      return;
    }
    // No unique exact match — fall back to activating whichever product
    // is currently keyboard-focused in the grid, if any.
    const focused = products[focusedProductIndex];
    if (focused) handleProductActivate(focused);
  }

  const totals = cart.reduce(
    (acc, l) => {
      const { subtotalMinor, taxMinor, totalMinor } = computeLineTotal(
        { price_minor: l.unitPriceMinor, currency: l.currency, is_weighted: l.isWeighted, tax_rate_basis_points: l.taxRateBasisPoints },
        l.quantity
      );
      acc.subtotalMinor += subtotalMinor;
      acc.taxMinor += taxMinor;
      acc.totalMinor += totalMinor;
      return acc;
    },
    { subtotalMinor: 0, taxMinor: 0, totalMinor: 0 }
  );
  const discountMinor = 0; // Phase 9B scope — shown for visibility per the CTO plan, not yet computable here.
  const currency = cart[0]?.currency ?? "EUR";

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

      <div className="flex-1 grid grid-cols-[10rem_1fr_22rem] overflow-hidden">
        <CategorySidebar categories={categories} selectedCategoryId={selectedCategoryId} onSelect={setSelectedCategoryId} />

        <main className="p-3 overflow-y-auto border-r">
          <input
            ref={searchInputRef}
            className="w-full border rounded px-2 py-1 mb-2"
            placeholder="Search name, SKU, or scan a barcode…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={handleSearchKeyDown}
          />
          {products.length === 0 && (search.length > 0 || selectedCategoryId !== null) && (
            <p className="text-sm text-gray-400 py-4 text-center">No products found.</p>
          )}
          <div className="grid grid-cols-3 gap-2">
            {products.map((p, i) => (
              <button
                key={p.id}
                onClick={() => handleProductActivate(p)}
                className={`border rounded p-2 text-left hover:bg-gray-50 ${
                  i === focusedProductIndex ? "ring-2 ring-black" : ""
                }`}
              >
                <div className="text-sm font-medium">{p.name}</div>
                <div className="text-xs text-gray-500">
                  {formatMoney(p.price_minor, p.currency)}
                  {p.is_weighted ? " / kg" : ""}
                </div>
              </button>
            ))}
          </div>
        </main>

        <aside data-testid="cart-summary" className="p-3 overflow-y-auto flex flex-col">
          {lastReceipt && <p className="text-sm text-green-700 mb-2">{lastReceipt}</p>}
          {checkoutMutation.isError && (
            <p className="text-sm text-red-600 mb-2">{(checkoutMutation.error as Error).message}</p>
          )}
          <div className="flex-1">
            <CartPanel
              lines={cart}
              selectedLineId={selectedLineId}
              onSelectLine={setSelectedLineId}
              onChangeQuantity={changeCartQuantity}
              onRemoveLine={removeCartLine}
              onClearCart={clearCart}
            />
          </div>
          <div className="border-t pt-2 mt-2 space-y-1 text-sm">
            <div className="flex justify-between text-gray-500">
              <span>Subtotal</span>
              <span>{formatMoney(totals.subtotalMinor, currency)}</span>
            </div>
            <div className="flex justify-between text-gray-500">
              <span>Discount</span>
              <span>{formatMoney(discountMinor, currency)}</span>
            </div>
            <div className="flex justify-between text-gray-500">
              <span>Tax</span>
              <span>{formatMoney(totals.taxMinor, currency)}</span>
            </div>
            <div className="flex justify-between font-semibold text-base">
              <span>Total</span>
              <span>{formatMoney(totals.totalMinor, currency)}</span>
            </div>
          </div>
          <div className="flex gap-2 mt-2">
            <button
              className="flex-1 px-4 py-2 bg-black text-white rounded disabled:opacity-50"
              disabled={cart.length === 0 || checkoutMutation.isPending}
              onClick={() => checkoutMutation.mutate()}
            >
              {checkoutMutation.isPending ? "Processing…" : "CASH — Complete Sale"}
            </button>
          </div>
          <button className="w-full px-4 py-2 bg-gray-200 rounded mt-2" disabled>
            CARD (mock provider — Phase 10 gateway pending)
          </button>
        </aside>
      </div>

      <footer className="border-t px-3 py-1.5 text-xs text-gray-400 flex gap-4">
        <span>F1 Search</span>
        <span className="opacity-40">F3 Hold (Phase 9B)</span>
        <span className="opacity-40">F4 Recall (Phase 9B)</span>
        <span>↑↓ navigate results · Enter add · Esc clear search</span>
      </footer>

      {weightDialogProduct && (
        <WeightEntryDialog
          productName={weightDialogProduct.name}
          pricePerKiloMinor={weightDialogProduct.price_minor}
          currency={weightDialogProduct.currency}
          onCancel={() => setWeightDialogProduct(null)}
          onConfirm={(grams) => {
            addToCart(weightDialogProduct, grams);
            setWeightDialogProduct(null);
            setSearch("");
          }}
        />
      )}
    </div>
  );
}
