import { useConnectivityStore } from "../store/connectivity";

const LABEL: Record<string, string> = {
  ONLINE: "ONLINE",
  OFFLINE: "OFFLINE",
  SYNCING: "SYNCING",
  SYNC_ERROR: "SYNC ERROR",
  PARTIALLY_CONNECTED: "PARTIALLY CONNECTED",
};

const COLOR: Record<string, string> = {
  ONLINE: "bg-green-100 text-green-800",
  OFFLINE: "bg-gray-200 text-gray-700",
  SYNCING: "bg-blue-100 text-blue-800",
  SYNC_ERROR: "bg-red-100 text-red-800",
  PARTIALLY_CONNECTED: "bg-amber-100 text-amber-800",
};

export function ConnectivityBadge() {
  const state = useConnectivityStore((s) => s.state);
  const pending = useConnectivityStore((s) => s.pendingCount);
  return (
    <div className={`px-3 py-1 rounded-full text-xs font-semibold ${COLOR[state]}`}>
      {LABEL[state]}
      {pending > 0 ? ` · ${pending} pending` : ""}
    </div>
  );
}
