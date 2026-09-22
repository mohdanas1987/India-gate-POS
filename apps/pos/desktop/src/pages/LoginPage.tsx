import { useState } from "react";
import { useNavigate } from "react-router-dom";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8100";

function isElectron(): boolean {
  return typeof window !== "undefined" && !!window.electronAPI;
}

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      let res: Response;
      try {
        res = await fetch(`${API_BASE}/api/v1/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password }),
        });
      } catch (networkErr) {
        // Phase 22.1 (CTO gate: "device starts offline -> cashier wants
        // to open shift -> checkout" was previously unsupported). A
        // genuine network failure (server unreachable), not a login
        // rejection — try the offline path, which only succeeds if this
        // exact device previously cached credentials from a real online
        // login. Outside Electron there is no offline path at all: the
        // Playwright smoke test's plain browser has no local device
        // database to check against.
        if (!isElectron()) throw networkErr;
        const offline = await window.electronAPI!.offlineLogin(email, password);
        if (!offline.ok) throw new Error(offline.error);
        navigate("/pos");
        return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `Login failed (HTTP ${res.status})`);
      }
      const data = await res.json();

      if (isElectron()) {
        // CTO audit finding #19: the access token used to be written to
        // renderer localStorage, which is not acceptable for a production
        // Electron app (any script that ran in the renderer, or anyone
        // reading the app's on-disk storage, could read it). It now goes
        // to the main process via IPC, which encrypts it at rest with the
        // OS keychain (Electron's safeStorage) — see electron/main.ts.
        // The renderer never sees it again after this call.
        await window.electronAPI!.saveAuthToken(data.access_token);
        // Phase 22.1: this is the ONLY place a plaintext password is ever
        // available — cache a local bcrypt hash of it now, immediately
        // after the server has proven it correct, so a future offline
        // login on this same device has something to verify against.
        // The plaintext password itself is never persisted anywhere.
        await window.electronAPI!.cacheOfflineCredential(email, password);
        const meRes = await window.electronAPI!.authedRequest({ path: "/api/v1/auth/me" });
        if (meRes.ok) {
          const me = meRes.body as { user_id: number; tenant_id: number; store_id: number | null; role: string };
          await window.electronAPI!.saveAuthContext(me);
        }
      } else {
        // Outside Electron (the Playwright smoke test's plain-browser
        // renderer has no secure-storage IPC to call) localStorage remains
        // the only place a token CAN live — disclosed limitation, not
        // silently different behavior.
        localStorage.setItem("igpos_dev_token", data.access_token);
      }
      navigate("/pos");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="h-screen flex items-center justify-center">
      <form onSubmit={handleSubmit} className="w-80 space-y-3 border rounded p-6">
        <h1 className="text-lg font-semibold">India Gate POS</h1>
        <input
          className="w-full border rounded px-2 py-1"
          type="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <input
          className="w-full border rounded px-2 py-1"
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button type="submit" disabled={loading} className="w-full bg-black text-white rounded py-2 disabled:opacity-50">
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
