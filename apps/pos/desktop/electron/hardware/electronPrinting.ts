/**
 * Real implementations of the printer/drawer/barcode-label providers,
 * ported from the legacy main.js silent-print techniques (Phase 0 audit
 * §9). These run in the Electron MAIN process only.
 */
import { BrowserWindow } from "electron";
import type { PrinterProvider, CashDrawerProvider, BarcodePrinterProvider, ReceiptData } from "./types";

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export class ElectronPrinterProvider implements PrinterProvider {
  constructor(private logoBase64: string) {}

  /**
   * CTO audit finding #18: `printReceipt(html)` used to accept an
   * arbitrary HTML string straight from the renderer and hand it to the
   * printer unrestricted — fine for a controlled first pass, but the
   * finding is right that the final version shouldn't let a compromised
   * or buggy renderer inject arbitrary printer content. This is the
   * structured contract: the renderer sends DATA (order number, lines,
   * totals), and the actual HTML is generated here, in the trusted main
   * process, with every field escaped.
   */
  async printReceipt(receipt: ReceiptData): Promise<void> {
    const rows = receipt.lines
      .map(
        (l) =>
          `<tr><td>${escapeHtml(l.name)}</td><td>${l.quantity}</td><td>€${(l.lineTotalMinor / 100).toFixed(2)}</td></tr>`
      )
      .join("");
    const win = new BrowserWindow({ show: false });
    const full = `<html><style>
        @page{ size:auto; margin:-5mm 3mm 3mm 2mm }
        *{font-weight:400!important;text-transform:uppercase;font-size:0.85rem!important;font-family:system-ui!important}
      </style>
      <body style="width:32%!important;margin:0px!important;padding:0px!important;">
        <div style="text-align:center"><img src="data:image/png;base64,${this.logoBase64}" height="100"/></div>
        <h3>${escapeHtml(receipt.orderLabel)}</h3>
        <table>${rows}</table>
        <p><strong>Total: €${(receipt.totalMinor / 100).toFixed(2)}</strong></p>
        <p>Generated: ${new Date().toLocaleString()}</p>
      </body></html>`;
    await this.silentPrint(win, full);
  }

  async printReport(html: string): Promise<void> {
    const win = new BrowserWindow({ show: false });
    await this.silentPrint(win, html, { printBackground: true });
  }

  private silentPrint(win: BrowserWindow, html: string, opts: Electron.WebContentsPrintOptions = {}): Promise<void> {
    return new Promise((resolve) => {
      win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
      win.webContents.on("did-finish-load", () => {
        win.webContents.print({ silent: true, ...opts }, (success, error) => {
          if (!success && error) console.error("Print failed:", error);
          win.close();
          resolve();
        });
      });
    });
  }
}

export class ElectronCashDrawerProvider implements CashDrawerProvider {
  async openDrawer(): Promise<void> {
    // Legacy technique: printing a near-empty page to the receipt
    // printer triggers its cash-drawer-kick pin. Preserved as-is —
    // it's a real, working hardware trick, not a placeholder.
    const win = new BrowserWindow({ show: false });
    const html = `<html><body><style>@page{size:auto;margin:1mm 0mm 0mm 1mm;}</style></body></html>`;
    return new Promise((resolve) => {
      win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
      win.webContents.on("did-finish-load", () => {
        win.webContents.print({ silent: true, pageSize: { height: 2000, width: 40000 } }, (success, error) => {
          if (!success && error) console.error("Drawer kick failed:", error);
          win.close();
          resolve();
        });
      });
    });
  }
}

export class ElectronBarcodePrinterProvider implements BarcodePrinterProvider {
  async printBarcodeLabel({ name, price, code }: { name: string; price: string; code: string }): Promise<void> {
    // bwip-js barcode SVG generation is unchanged from the legacy
    // implementation — it worked, no reason to replace it.
    const bwipjs = await import("bwip-js");
    const svg = await bwipjs.toSVG({
      bcid: "code128",
      text: code,
      scale: 2,
      height: 8,
      includetext: true,
      textxalign: "center",
      textyalign: "below",
    });
    const win = new BrowserWindow({ show: false });
    const html = `<html><style>@page{size:auto;margin:1mm 0mm 0mm 1mm}*{font-family:system-ui!important}</style>
      <body><div style="width:20%;margin-left:60px;">
        <div style="font-size:0.8rem;margin-bottom:8px;padding-bottom:2px;border-bottom:2px solid;font-weight:600">${name}</div>
        ${svg}
        <div style="text-align:center;margin-top:-25px"><h2 style="font-size:2rem;font-weight:600">€${price}</h2></div>
      </div></body></html>`;
    return new Promise((resolve) => {
      win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
      win.webContents.on("did-finish-load", () => {
        win.webContents.print({ silent: true }, (success, error) => {
          if (!success && error) console.error("Label print failed:", error);
          win.close();
          resolve();
        });
      });
    });
  }
}
