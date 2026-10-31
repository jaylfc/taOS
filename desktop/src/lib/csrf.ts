/**
 * CSRF double-submit helper.
 *
 * The backend's CSRFMiddleware sets a non-HttpOnly `csrf_token` cookie; the
 * `verify_csrf` dependency requires a matching `X-CSRF-Token` header on
 * cookie-authenticated mutating requests. This helper reads that cookie and
 * attaches the header so the SPA's mutating calls satisfy the check.
 *
 * Combined with the session cookie now being SameSite=Strict (browser-level
 * CSRF defense), this is the double-submit second layer.
 */

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function getCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  // Take the last csrf_token: the server reads the last same-name cookie, and
  // re-issues its own host-only cookie as the newest, so it sorts after a
  // parent-domain cookie planted from a sibling subdomain.
  const matches = [...document.cookie.matchAll(/(?:^|;\s*)csrf_token=([^;]*)/g)];
  const value = matches[matches.length - 1]?.[1];
  return value ? decodeURIComponent(value) : null;
}

/**
 * Return request init with the CSRF header attached when the method mutates
 * state and a token cookie is present. Non-mutating methods pass through
 * unchanged.
 */
export function withCsrf(init?: RequestInit): RequestInit | undefined {
  const method = (init?.method ?? "GET").toUpperCase();
  if (!MUTATING.has(method)) return init;
  const token = getCsrfToken();
  if (!token) return init;
  const headers = new Headers(init?.headers);
  if (!headers.has("X-CSRF-Token")) headers.set("X-CSRF-Token", token);
  return { ...init, headers };
}
