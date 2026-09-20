/**
 * Phase 10 — Hardware Abstraction interfaces (plan §57).
 *
 * Every concrete implementation below is a direct, hardened port of
 * working techniques found in the legacy `main.js` during the Phase 0
 * audit (silent-print cash drawer kick, hidden-BrowserWindow receipt/
 * barcode-label printing, second-display customer window). The
 * TECHNIQUES are preserved because they work in production today; what
 * changes is that business/UI code now depends on these interfaces
 * instead of reaching into Electron APIs directly, and the Electron
 * security posture around them is hardened (see main.ts).
 */

/**
 * Structured receipt contract (CTO audit finding #18): the renderer sends
 * DATA, never raw HTML, for anything that reaches the physical printer via
 * printReceipt — the trusted main process is what turns this into markup.
 * `printReport` still takes raw HTML because reports are generated
 * server/main-process-side already in every caller today; if a renderer-
 * originated report ever needs printing, it should get the same
 * structured treatment rather than reusing this escape hatch.
 */
export interface ReceiptLine {
  name: string;
  quantity: number;
  lineTotalMinor: number;
}

export interface ReceiptData {
  orderLabel: string;
  lines: ReceiptLine[];
  totalMinor: number;
}

export interface PrinterProvider {
  printReceipt(receipt: ReceiptData): Promise<void>;
  printReport(html: string): Promise<void>;
}

export interface CashDrawerProvider {
  openDrawer(): Promise<void>;
}

export interface BarcodePrinterProvider {
  printBarcodeLabel(args: { name: string; price: string; code: string }): Promise<void>;
}

export interface BarcodeScannerProvider {
  /**
   * Legacy finding: no explicit scanner integration exists in main.js —
   * scanners are almost certainly wired as keyboard-wedge input consumed
   * directly by the React app (standard for USB/Bluetooth barcode
   * scanners). This interface exists so that assumption is made explicit
   * and testable, and so a future serial/HID scanner can be added without
   * changing calling code. NOT YET CONFIRMED against the actual legacy
   * frontend source — flagged for Phase 10 deep-dive.
   */
  onScan(callback: (code: string) => void): () => void; // returns an unsubscribe function
}

export interface CustomerDisplayProvider {
  updateDisplay(content: unknown): void;
}

export interface ScaleProvider {
  /** No legacy implementation found — genuinely new (plan §58's weighted
   * products are supported by data model already; physical scale
   * integration is unimplemented in both old and new system). */
  readWeightGrams(): Promise<number>;
}

export type PaymentResult =
  | { status: "approved"; providerReference?: string }
  | { status: "declined"; reason: string }
  | { status: "error"; message: string };

export interface PaymentProvider {
  charge(amountMinor: number, currency: string): Promise<PaymentResult>;
}
