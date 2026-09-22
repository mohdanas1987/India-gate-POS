/**
 * Preload — the ONLY bridge between renderer and main process. Narrow,
 * explicit surface (same contextBridge pattern the legacy app already
 * used correctly — preserved, just now meaningful because nodeIntegration
 * is off).
 */
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("electronAPI", {
  drawCash: () => ipcRenderer.invoke("hardware:draw-cash"),
  printReceipt: (receipt: { orderLabel: string; lines: { name: string; quantity: number; lineTotalMinor: number }[]; totalMinor: number }) =>
    ipcRenderer.invoke("hardware:print-receipt", receipt),
  printReport: (html: string) => ipcRenderer.invoke("hardware:print-report", html),
  printBarcodeLabel: (args: { name: string; price: string; code: string }) =>
    ipcRenderer.invoke("hardware:print-barcode-label", args),
  reloadDisplays: (content: unknown) => ipcRenderer.send("pos:reload-displays", content),
  onDisplayData: (callback: (data: unknown) => void) =>
    ipcRenderer.on("pos:data-received", (_evt, data) => callback(data)),
  relaunch: () => ipcRenderer.send("app:relaunch"),

  // Phase 8 rebuild — offline-first surface (CTO audit c4bfb82, findings
  // #5/#6/#8/#19). The renderer never touches better-sqlite3 or the auth
  // token directly; everything goes through these narrow IPC calls to the
  // main process, which owns the local database and the encrypted token.
  saveAuthToken: (token: string) => ipcRenderer.invoke("auth:save-token", token),
  clearAuthToken: () => ipcRenderer.invoke("auth:clear-token"),
  saveAuthContext: (ctx: { user_id: number; tenant_id: number; store_id: number | null; role: string }) =>
    ipcRenderer.invoke("auth:save-context", ctx),
  getCachedAuthContext: () => ipcRenderer.invoke("auth:get-cached-context"),
  syncCatalog: () => ipcRenderer.invoke("catalog:sync"),
  searchLocalProducts: (query: string | null, categoryId?: number | null) =>
    ipcRenderer.invoke("catalog:search", query, categoryId ?? null),
  listLocalCategories: () => ipcRenderer.invoke("catalog:list-categories"),
  cacheOpenSession: (session: {
    session_id: number; register_id: number; store_id: number; tenant_id: number; cashier_user_id: number; opened_at: string;
  }) => ipcRenderer.invoke("cash:cache-session", session),
  getCachedOpenSession: () => ipcRenderer.invoke("cash:get-cached-session"),
  checkoutOffline: (cart: { productId: number; quantity: number }[]) => ipcRenderer.invoke("checkout:offline", cart),

  // Phase 22.1 — offline shift-start (CTO gate: "device starts offline,
  // cashier wants to open shift, checkout" was previously unsupported).
  cacheOfflineCredential: (email: string, password: string) =>
    ipcRenderer.invoke("auth:cache-offline-credential", { email, password }),
  offlineLogin: (email: string, password: string) => ipcRenderer.invoke("auth:offline-login", { email, password }),
  openShiftOffline: (registerId: number, openingCashMinor: number) =>
    ipcRenderer.invoke("cash:open-shift-offline", { registerId, openingCashMinor }),
  getConnectivityState: () => ipcRenderer.invoke("sync:get-connectivity-state"),
  onConnectivityChanged: (callback: (state: string) => void) =>
    ipcRenderer.on("sync:connectivity-changed", (_evt, state) => callback(state)),
  authedRequest: (args: { path: string; method?: string; body?: unknown }) =>
    ipcRenderer.invoke("api:authed-request", args),
});
