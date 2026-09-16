import { HashRouter, Routes, Route, Link, Navigate } from "react-router-dom";
import { PosPage } from "./pages/PosPage";
import { WebsiteOrdersPage } from "./pages/WebsiteOrdersPage";
import { LoginPage } from "./pages/LoginPage";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8100";

function RequireAuth({ children }: { children: JSX.Element }) {
  const token = localStorage.getItem("igpos_dev_token");
  if (!token) return <Navigate to="/login" replace />;
  return children;
}

export function App() {
  const token = localStorage.getItem("igpos_dev_token") ?? "";

  return (
    <HashRouter>
      <nav className="flex gap-4 p-2 border-b text-sm">
        <Link to="/pos">POS</Link>
        <Link to="/website-orders">Website Orders</Link>
      </nav>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/pos"
          element={
            <RequireAuth>
              <PosPage />
            </RequireAuth>
          }
        />
        <Route
          path="/website-orders"
          element={
            <RequireAuth>
              <WebsiteOrdersPage apiBase={API_BASE} token={token} />
            </RequireAuth>
          }
        />
        <Route path="/pos/customer" element={<div className="p-6 text-2xl">Customer display</div>} />
        <Route path="*" element={<Navigate to="/pos" replace />} />
      </Routes>
    </HashRouter>
  );
}
