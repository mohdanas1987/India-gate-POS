/**
 * Phase 9A correction gate (CTO review of d6bad7c, finding #4 — cart-line
 * identity). `crypto.randomUUID()` is available in every environment this
 * renderer actually runs in (Electron's Chromium renderer, and Vite's dev
 * server over http://127.0.0.1, which Chromium treats as a secure context
 * for Web Crypto purposes) — but a plain, dependency-free fallback is kept
 * here rather than assuming that unconditionally, since a cart-line id
 * failing to generate would be a hard crash on the single most central
 * piece of POS state.
 */
export function generateCartLineId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // Fallback: not cryptographically strong, but only needs to be unique
  // within one cart's lifetime, never persisted or trusted for security.
  return `line-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
