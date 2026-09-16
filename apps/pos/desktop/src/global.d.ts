export {};

declare global {
  interface Window {
    electronAPI: {
      drawCash: () => Promise<void>;
      printReceipt: (html: string) => Promise<void>;
      printReport: (html: string) => Promise<void>;
      printBarcodeLabel: (args: { name: string; price: string; code: string }) => Promise<void>;
      reloadDisplays: (content: unknown) => void;
      onDisplayData: (callback: (data: unknown) => void) => void;
      relaunch: () => void;
    };
  }
}
