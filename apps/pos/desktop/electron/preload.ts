/**
 * Preload — the ONLY bridge between renderer and main process. Narrow,
 * explicit surface (same contextBridge pattern the legacy app already
 * used correctly — preserved, just now meaningful because nodeIntegration
 * is off).
 */
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("electronAPI", {
  drawCash: () => ipcRenderer.invoke("hardware:draw-cash"),
  printReceipt: (html: string) => ipcRenderer.invoke("hardware:print-receipt", html),
  printReport: (html: string) => ipcRenderer.invoke("hardware:print-report", html),
  printBarcodeLabel: (args: { name: string; price: string; code: string }) =>
    ipcRenderer.invoke("hardware:print-barcode-label", args),
  reloadDisplays: (content: unknown) => ipcRenderer.send("pos:reload-displays", content),
  onDisplayData: (callback: (data: unknown) => void) =>
    ipcRenderer.on("pos:data-received", (_evt, data) => callback(data)),
  relaunch: () => ipcRenderer.send("app:relaunch"),
});
