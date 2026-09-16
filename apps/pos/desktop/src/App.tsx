import { HashRouter, Routes, Route, Link } from "react-router-dom";
import { PosPage } from "./pages/PosPage";
import { WebsiteOrdersPage } from "./pages/WebsiteOrdersPage";

// Dev-only placeholder token/apiBase wiring — real auth flow (login screen
// calling POST /api/v1/auth/login and storing the token securely) is a
// follow-up task, not yet built. Flagged in the phase status report.
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8100";

export function App() {
  const token = localStorage.getItem("igpos_dev_token") ?? "";

  return (
    <HashRouter>
      <nav className="flex gap-4 p-2 border-b text-sm">
        <Link to="/pos">POS</Link>
        <Link to="/website-orders">Website Orders</Link>
      </nav>
      <Routes>
        <Route path="/pos" element={<PosPage />} />
        <Route path="/website-orders" element={<WebsiteOrdersPage apiBase={API_BASE} token={token} />} />
        <Route path="/pos/customer" element={<div className="p-6 text-2xl">Customer display</div>} />
        <Route path="*" element={<PosPage />} />
      </Routes>
    </HashRouter>
  );
}
