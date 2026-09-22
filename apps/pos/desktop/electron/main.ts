/**
 * Electron main process — Phase 2/9/10.
 *
 * SECURITY FIX vs legacy main.js (Phase 0 finding): the old app ran with
 * `nodeIntegration: true`, giving a compromised renderer full Node
 * access. Here:
 *   nodeIntegration: false
 *   contextIsolation: true
 *   sandbox: true
 * matching plan §56 exactly. The renderer's only bridge to the main
 * process is the narrow API in preload.ts.
 *
 * Everything else (multi-window customer display, silent-print hardware
 * IPC) is the same working behavior as the legacy app, now routed
 * through the Phase 10 provider interfaces instead of ad-hoc handlers.
 */
import { app, BrowserWindow, ipcMain, screen, safeStorage } from "electron";
import * as path from "path";
import * as fs from "fs";

import { ElectronPrinterProvider, ElectronCashDrawerProvider, ElectronBarcodePrinterProvider } from "./hardware/electronPrinting";
import {
  openLocalDb,
  replaceLocalCatalog,
  replaceLocalCategories,
  listLocalCategories,
  searchLocalProducts,
  saveLocalAuthContext,
  getLocalAuthContext,
  saveLocalRegisterSession,
  getLocalRegisterSession,
  saveKnownRegister,
  LocalProductRow,
  LocalCategoryRow,
} from "./sync/schema";
import { OutboxSyncEngine, ConnectivityState } from "./sync/outboxSync";
import { checkoutOffline, OfflineCheckoutError, OfflineCartLine } from "./offlineCheckout";
import { cacheOfflineCredential, verifyOfflineLogin, openShiftOffline, OfflineAuthError, OfflineShiftError } from "./offlineShift";

const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL;
const isDev = !!DEV_SERVER_URL;
const API_BASE = process.env.IGPOS_API_BASE ?? "http://localhost:8100";

let mainWindow: BrowserWindow | null = null;
const secondaryWindows: BrowserWindow[] = [];

// --- Phase 8 rebuild: local-first offline engine, owned by the main
// process because better-sqlite3 is a native module that cannot load in
// the sandboxed (nodeIntegration:false) renderer. This is what was
// entirely missing before (CTO audit finding #6): "the sync engine is
// currently library code, not an operating POS subsystem."
const localDb = openLocalDb(path.join(app.getPath("userData"), "pos-local.db"));

// CTO audit finding #19: an access token in renderer localStorage is not
// acceptable for the final app. Electron's safeStorage (OS keychain-backed
// encryption) is used instead, and the token never touches localStorage —
// the renderer only ever calls `secureAuth.*` IPC methods and never sees
// the raw token value directly stored itself outside memory.
const TOKEN_FILE = path.join(app.getPath("userData"), "auth-token.enc");
let cachedAccessToken: string | null = null;

function saveTokenSecurely(token: string): void {
  cachedAccessToken = token;
  if (safeStorage.isEncryptionAvailable()) {
    fs.writeFileSync(TOKEN_FILE, safeStorage.encryptString(token));
  }
  // If OS-level encryption isn't available (e.g. some Linux CI/headless
  // environments), the token still only lives in this main-process
  // variable for the session — never written to disk in plaintext, and
  // never exposed to the renderer's localStorage.
}

function loadTokenSecurely(): string | null {
  if (cachedAccessToken) return cachedAccessToken;
  try {
    if (safeStorage.isEncryptionAvailable() && fs.existsSync(TOKEN_FILE)) {
      cachedAccessToken = safeStorage.decryptString(fs.readFileSync(TOKEN_FILE));
    }
  } catch {
    cachedAccessToken = null;
  }
  return cachedAccessToken;
}

function clearTokenSecurely(): void {
  cachedAccessToken = null;
  try {
    if (fs.existsSync(TOKEN_FILE)) fs.unlinkSync(TOKEN_FILE);
  } catch {
    /* best-effort */
  }
}

let connectivityState: ConnectivityState = "OFFLINE";
const outboxEngine = new OutboxSyncEngine(localDb, API_BASE, loadTokenSecurely, (state) => {
  connectivityState = state;
  mainWindow?.webContents.send("sync:connectivity-changed", state);
});

function loadLogoBase64(): string {
  try {
    const p = path.join(__dirname, "../assets/logo.png");
    return fs.readFileSync(p).toString("base64");
  } catch {
    return ""; // logo is optional in dev; receipts print without it rather than crashing
  }
}

const printer = new ElectronPrinterProvider(loadLogoBase64());
const drawer = new ElectronCashDrawerProvider();
const barcodePrinter = new ElectronBarcodePrinterProvider();

function hardenedWebPreferences(preloadPath: string): Electron.WebPreferences {
  return {
    preload: preloadPath,
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    devTools: isDev,
  };
}

app.on("ready", () => {
  const preloadPath = path.join(__dirname, "preload.js");
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;

  mainWindow = new BrowserWindow({
    width,
    height,
    autoHideMenuBar: true,
    webPreferences: hardenedWebPreferences(preloadPath),
  });

  mainWindow.loadURL(DEV_SERVER_URL ?? `file://${path.join(__dirname, "../dist/index.html")}`);
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  mainWindow.once("ready-to-show", () => mainWindow?.show());

  outboxEngine.start();

  const displays = screen.getAllDisplays();
  const customerDisplay = displays.length > 1 ? displays[1] : null;
  if (customerDisplay) {
    const customerWindow = new BrowserWindow({
      width: customerDisplay.size.width,
      height: customerDisplay.size.height,
      x: customerDisplay.bounds.x,
      y: customerDisplay.bounds.y,
      frame: false,
      fullscreen: true,
      alwaysOnTop: true,
      webPreferences: hardenedWebPreferences(preloadPath),
    });
    customerWindow.loadURL(`${DEV_SERVER_URL ?? `file://${path.join(__dirname, "../dist/index.html")}`}#/pos/customer`);
    secondaryWindows.push(customerWindow);
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

// --- IPC: routed through the Phase 10 provider interfaces, not inline ---
ipcMain.handle("hardware:draw-cash", async () => {
  await drawer.openDrawer();
});

ipcMain.handle("hardware:print-receipt", async (_evt, receipt: import("./hardware/types").ReceiptData) => {
  await printer.printReceipt(receipt);
});

ipcMain.handle("hardware:print-report", async (_evt, html: string) => {
  await printer.printReport(html);
});

ipcMain.handle("hardware:print-barcode-label", async (_evt, args: { name: string; price: string; code: string }) => {
  await barcodePrinter.printBarcodeLabel(args);
});

ipcMain.on("pos:reload-displays", (_evt, content: unknown) => {
  secondaryWindows.forEach((win) => win.webContents.send("pos:data-received", content));
});

ipcMain.on("app:relaunch", () => {
  app.relaunch();
  app.exit();
});

// --- Phase 8 rebuild: offline-first IPC surface ---

ipcMain.handle("auth:save-token", (_evt, token: string) => {
  saveTokenSecurely(token);
});

ipcMain.handle("auth:clear-token", () => {
  clearTokenSecurely();
});

ipcMain.handle("auth:save-context", (_evt, ctx: { user_id: number; tenant_id: number; store_id: number | null; role: string }) => {
  saveLocalAuthContext(localDb, ctx);
});

ipcMain.handle("auth:get-cached-context", () => {
  return getLocalAuthContext(localDb) ?? null;
});

ipcMain.handle("catalog:sync", async () => {
  const token = loadTokenSecurely();
  if (!token) throw new Error("Cannot sync catalog: not logged in");

  // Phase 9A: the category sidebar needs an offline snapshot of
  // categories too (see schema.ts's local_categories table docstring) —
  // pulled in the same sync action as the product catalog so the two
  // never drift out of sync with each other on the device.
  const categoriesRes = await fetch(`${API_BASE}/api/v1/categories`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!categoriesRes.ok) throw new Error(`Category sync failed: HTTP ${categoriesRes.status}`);
  const categories = (await categoriesRes.json()) as Array<{ id: number; name: string; slug: string }>;
  const categoryRows: LocalCategoryRow[] = categories.map((c) => ({ id: c.id, name: c.name, slug: c.slug }));
  replaceLocalCategories(localDb, categoryRows);

  const res = await fetch(`${API_BASE}/api/v1/products?limit=1000`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`Catalog sync failed: HTTP ${res.status}`);
  const products = (await res.json()) as Array<{
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
  }>;
  const rows: LocalProductRow[] = products.map((p) => ({
    id: p.id,
    name: p.name,
    sku: p.sku,
    price_minor: p.price_minor,
    currency: p.currency,
    unit: p.unit,
    is_weighted: p.is_weighted ? 1 : 0,
    category_id: p.category_id,
    tax_rate_basis_points: p.tax_rate_basis_points,
    barcodes: JSON.stringify(p.barcodes ?? []),
  }));
  replaceLocalCatalog(localDb, rows);
  return { count: rows.length, categoryCount: categoryRows.length };
});

ipcMain.handle("catalog:search", (_evt, query: string | null, categoryId: number | null) => {
  return searchLocalProducts(localDb, query, categoryId ?? null);
});

ipcMain.handle("catalog:list-categories", () => {
  return listLocalCategories(localDb);
});

ipcMain.handle("cash:cache-session", (_evt, session: {
  session_id: number; register_id: number; store_id: number; tenant_id: number; cashier_user_id: number; opened_at: string;
}) => {
  saveLocalRegisterSession(localDb, session);
  // Phase 22.1: this device has now been PROVEN, online, to be
  // authorized for this register (the server's resolve_authorized_register
  // already checked tenant/store/register ownership before this session
  // could open). Remembering it is what lets an offline shift-start later
  // reopen the SAME register without re-trusting an unverified id.
  saveKnownRegister(localDb, session.register_id);
});

ipcMain.handle("cash:get-cached-session", () => {
  return getLocalRegisterSession(localDb) ?? null;
});

// --- Phase 22.1: offline shift-start IPC surface ---

ipcMain.handle("auth:cache-offline-credential", (_evt, args: { email: string; password: string }) => {
  cacheOfflineCredential(localDb, args.email, args.password);
});

ipcMain.handle("auth:offline-login", (_evt, args: { email: string; password: string }) => {
  try {
    return { ok: true as const, context: verifyOfflineLogin(localDb, args.email, args.password) };
  } catch (err) {
    if (err instanceof OfflineAuthError) return { ok: false as const, error: err.message };
    throw err;
  }
});

ipcMain.handle("cash:open-shift-offline", (_evt, args: { registerId: number; openingCashMinor: number }) => {
  try {
    return { ok: true as const, result: openShiftOffline(localDb, args.registerId, args.openingCashMinor) };
  } catch (err) {
    if (err instanceof OfflineShiftError) return { ok: false as const, error: err.message };
    throw err;
  }
});

ipcMain.handle("checkout:offline", (_evt, cart: OfflineCartLine[]) => {
  try {
    return { ok: true as const, receipt: checkoutOffline(localDb, cart) };
  } catch (err) {
    if (err instanceof OfflineCheckoutError) {
      return { ok: false as const, error: err.message };
    }
    throw err;
  }
});

ipcMain.handle("sync:get-connectivity-state", () => connectivityState);

// CTO audit finding #19: an auth token in renderer localStorage is not
// acceptable for the final app. This generic passthrough means the
// renderer never needs to hold the raw token at all when running inside
// Electron — every authenticated call to the API is made from HERE, using
// the token from encrypted storage, and only the response body crosses
// back over the IPC boundary.
ipcMain.handle(
  "api:authed-request",
  async (_evt, args: { path: string; method?: string; body?: unknown }) => {
    const token = loadTokenSecurely();
    if (!token) throw new Error("Not logged in");
    const res = await fetch(`${API_BASE}${args.path}`, {
      method: args.method ?? "GET",
      headers: {
        Authorization: `Bearer ${token}`,
        ...(args.body ? { "Content-Type": "application/json" } : {}),
      },
      body: args.body ? JSON.stringify(args.body) : undefined,
    });
    const json = await res.json().catch(() => null);
    return { ok: res.ok, status: res.status, body: json };
  }
);
