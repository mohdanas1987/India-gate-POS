/**
 * Small shared helper so every screen resolves "make an authenticated API
 * call" the same way, now that the token lives in two different places
 * depending on environment (CTO audit finding #19):
 *   - Inside Electron: the raw token never enters the renderer at all —
 *     the call is proxied through the main process (electron/main.ts,
 *     `api:authed-request`), which reads it from OS-encrypted storage.
 *   - Outside Electron (the Playwright smoke test's plain-browser
 *     renderer, which has no secure-storage IPC to call): localStorage
 *     remains the only place a token CAN live, so this falls back to a
 *     normal fetch with a Bearer header from there.
 */
export function isElectron(): boolean {
  return typeof window !== "undefined" && !!window.electronAPI;
}

export interface AuthedResult<T> {
  ok: boolean;
  status: number;
  body: T;
}

export async function authedFetch<T = unknown>(
  path: string,
  init: { method?: string; body?: unknown } = {}
): Promise<AuthedResult<T>> {
  if (isElectron()) {
    const result = await window.electronAPI!.authedRequest({ path, method: init.method, body: init.body });
    return result as AuthedResult<T>;
  }
  const token = localStorage.getItem("igpos_dev_token") ?? "";
  const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8100";
  const res = await fetch(`${API_BASE}${path}`, {
    method: init.method ?? "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init.body ? { "Content-Type": "application/json" } : {}),
    },
    body: init.body ? JSON.stringify(init.body) : undefined,
  });
  const body = (await res.json().catch(() => null)) as T;
  return { ok: res.ok, status: res.status, body };
}

/** True once we can tell there IS a logged-in session (either environment). */
export async function isAuthenticated(): Promise<boolean> {
  if (isElectron()) {
    const ctx = await window.electronAPI!.getCachedAuthContext();
    return !!ctx;
  }
  return !!localStorage.getItem("igpos_dev_token");
}

export async function logout(): Promise<void> {
  if (isElectron()) {
    await window.electronAPI!.clearAuthToken();
  } else {
    localStorage.removeItem("igpos_dev_token");
  }
}
