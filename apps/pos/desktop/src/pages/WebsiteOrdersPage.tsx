/**
 * Phase 11 — Website Orders screen (plan §19). Calls the real FastAPI
 * `/api/v1/website-orders` endpoint (app/api/v1/website_orders.py) — this
 * is a genuine API integration, not an embedded website page, per the
 * plan's explicit architectural requirement.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

interface WebsiteOrderRow {
  id: number;
  external_order_number: string;
  status: string;
  customer_name: string | null;
  payment_status: string;
  delivery_method: string;
  pending_website_sync: boolean;
}

const STATUS_FILTERS = [
  "All",
  "NEW",
  "CONFIRMED",
  "PROCESSING",
  "PACKING",
  "READY_FOR_DISPATCH",
  "OUT_FOR_DELIVERY",
  "DELIVERED",
  "COMPLETED",
  "CANCELLED",
  "REFUNDED",
];

async function fetchWebsiteOrders(apiBase: string, token: string, status: string, search: string, page: number) {
  const params = new URLSearchParams({ page: String(page), page_size: "25" });
  if (status !== "All") params.set("status", status);
  if (search) params.set("search", search);
  const res = await fetch(`${apiBase}/api/v1/website-orders?${params}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`Failed to load website orders: HTTP ${res.status}`);
  return res.json() as Promise<{ items: WebsiteOrderRow[]; total: number; page: number; page_size: number }>;
}

export function WebsiteOrdersPage({ apiBase, token }: { apiBase: string; token: string }) {
  const [status, setStatus] = useState("All");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const { data, isLoading, error } = useQuery({
    queryKey: ["website-orders", status, search, page],
    queryFn: () => fetchWebsiteOrders(apiBase, token, status, search, page),
  });

  return (
    <div className="p-4">
      <h1 className="text-lg font-semibold mb-3">Website Orders</h1>

      <div className="flex gap-2 mb-3">
        <input
          className="border rounded px-2 py-1 text-sm"
          placeholder="Search order #, customer, phone..."
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
        <select
          className="border rounded px-2 py-1 text-sm"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
        >
          {STATUS_FILTERS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      {isLoading && <p className="text-sm text-gray-500">Loading…</p>}
      {error && <p className="text-sm text-red-600">{(error as Error).message}</p>}

      {data && (
        <>
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="text-left border-b">
                <th className="py-1">Order #</th>
                <th>Customer</th>
                <th>Status</th>
                <th>Payment</th>
                <th>Delivery</th>
                <th>Sync</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((o) => (
                <tr key={o.id} className="border-b">
                  <td className="py-1">{o.external_order_number}</td>
                  <td>{o.customer_name ?? "—"}</td>
                  <td>{o.status}</td>
                  <td>{o.payment_status}</td>
                  <td>{o.delivery_method}</td>
                  <td>{o.pending_website_sync ? "⏳ pending sync" : "✓"}</td>
                </tr>
              ))}
              {data.items.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-4 text-center text-gray-400">
                    No website orders yet — this is expected until Phase 11's real website
                    integration is wired to an actual provider (currently the mock provider,
                    see Phase 0's open item on the India Gate website platform).
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          <div className="flex justify-between items-center mt-3 text-sm">
            <span>
              Page {data.page} — {data.total} total
            </span>
            <div className="flex gap-2">
              <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className="px-2 py-1 border rounded disabled:opacity-30">
                Prev
              </button>
              <button
                disabled={page * data.page_size >= data.total}
                onClick={() => setPage((p) => p + 1)}
                className="px-2 py-1 border rounded disabled:opacity-30"
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
