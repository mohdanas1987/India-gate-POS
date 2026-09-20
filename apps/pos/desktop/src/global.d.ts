export {};

declare global {
  interface Window {
    electronAPI: {
      drawCash: () => Promise<void>;
      printReceipt: (receipt: {
        orderLabel: string;
        lines: { name: string; quantity: number; lineTotalMinor: number }[];
        totalMinor: number;
      }) => Promise<void>;
      printReport: (html: string) => Promise<void>;
      printBarcodeLabel: (args: { name: string; price: string; code: string }) => Promise<void>;
      reloadDisplays: (content: unknown) => void;
      onDisplayData: (callback: (data: unknown) => void) => void;
      relaunch: () => void;

      // Phase 8 rebuild — offline-first surface.
      saveAuthToken: (token: string) => Promise<void>;
      clearAuthToken: () => Promise<void>;
      saveAuthContext: (ctx: { user_id: number; tenant_id: number; store_id: number | null; role: string }) => Promise<void>;
      getCachedAuthContext: () => Promise<{ user_id: number; tenant_id: number; store_id: number | null; role: string } | null>;
      syncCatalog: () => Promise<{ count: number }>;
      searchLocalProducts: (query: string) => Promise<
        Array<{
          id: number;
          name: string;
          sku: string | null;
          price_minor: number;
          currency: string;
          unit: string;
          is_weighted: number;
          tax_rate_basis_points: number | null;
          barcodes: string;
        }>
      >;
      cacheOpenSession: (session: {
        session_id: number;
        register_id: number;
        store_id: number;
        tenant_id: number;
        cashier_user_id: number;
        opened_at: string;
      }) => Promise<void>;
      getCachedOpenSession: () => Promise<{
        session_id: number;
        register_id: number;
        store_id: number;
        tenant_id: number;
        cashier_user_id: number;
        opened_at: string;
      } | null>;
      checkoutOffline: (cart: { productId: number; quantity: number }[]) => Promise<
        | {
            ok: true;
            receipt: {
              localOrderId: string;
              eventId: string;
              lines: Array<{
                productId: number;
                name: string;
                quantity: number;
                unitPriceMinor: number;
                lineSubtotalMinor: number;
                lineTaxMinor: number;
                lineTotalMinor: number;
              }>;
              subtotalMinor: number;
              taxMinor: number;
              totalMinor: number;
              currency: string;
              createdAt: string;
            };
          }
        | { ok: false; error: string }
      >;
      getConnectivityState: () => Promise<string>;
      onConnectivityChanged: (callback: (state: string) => void) => void;
      authedRequest: (args: { path: string; method?: string; body?: unknown }) => Promise<{
        ok: boolean;
        status: number;
        body: unknown;
      }>;
    };
  }
}
