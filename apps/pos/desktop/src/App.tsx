import { useEffect, useState } from "react";
import { HashRouter, Routes, Route, Link, Navigate } from "react-router-dom";
import { PosPage } from "./pages/PosPage";
import { WebsiteOrdersPage } from "./pages/WebsiteOrdersPage";
import { LoginPage } from "./pages/LoginPage";
import { isAuthenticated } from "./api/authedFetch";

/**
 * Rebuilt during the CTO-audit remediation pass: this used to read
 * `localStorage.getItem("igpos_dev_token")` synchronously, which broke
 * the moment the token stopped living in localStorage inside Electron
 * (see LoginPage.tsx / api/authedFetch.ts, finding #19). The check is now
 * async and environment-aware, with an explicit loading state instead of
 * assuming localStorage is where the answer always lives.
 */
function RequireAuth({ children }: { children: JSX.Element }) {
  const [state, setState] = useState<"checking" | "authed" | "anon">("checking");

  useEffect(() => {
    let cancelled = false;
    isAuthenticated().then((authed) => {
      if (!cancelled) setState(authed ? "authed" : "anon");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state === "checking") return null;
  if (state === "anon") return <Navigate to="/login" replace />;
  return children;
}

export function App() {
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
              <WebsiteOrdersPage />
            </RequireAuth>
          }
        />
        <Route path="/pos/customer" element={<div className="p-6 text-2xl">Customer display</div>} />
        <Route path="*" element={<Navigate to="/pos" replace />} />
      </Routes>
    </HashRouter>
  );
}
