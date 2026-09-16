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
import { app, BrowserWindow, ipcMain, screen } from "electron";
import * as path from "path";
import * as fs from "fs";

import { ElectronPrinterProvider, ElectronCashDrawerProvider, ElectronBarcodePrinterProvider } from "./hardware/electronPrinting";

const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL;
const isDev = !!DEV_SERVER_URL;

let mainWindow: BrowserWindow | null = null;
const secondaryWindows: BrowserWindow[] = [];

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

ipcMain.handle("hardware:print-receipt", async (_evt, html: string) => {
  await printer.printReceipt(html);
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
