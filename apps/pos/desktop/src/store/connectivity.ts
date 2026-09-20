/**
 * Plan §9/§82: the POS must clearly indicate ONLINE / OFFLINE / SYNCING /
 * SYNC ERROR / PARTIALLY CONNECTED at all times. This store is the single
 * source of truth the UI reads; it's updated by the OutboxSyncEngine's
 * onStateChange callback (wired in main.tsx).
 */
import { create } from "zustand";
import type { ConnectivityState } from "../../electron/sync/outboxSync";

interface ConnectivityStore {
  state: ConnectivityState;
  pendingCount: number;
  setState: (s: ConnectivityState) => void;
  setPendingCount: (n: number) => void;
}

export const useConnectivityStore = create<ConnectivityStore>((set) => ({
  state: "OFFLINE",
  pendingCount: 0,
  setState: (state) => set({ state }),
  setPendingCount: (pendingCount) => set({ pendingCount }),
}));
