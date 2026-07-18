/**
 * Auth-expired guard.
 *
 * Wraps window.fetch so any 401 response on an /api/* request fires a
 * `taos-session-expired` CustomEvent that LoginGate listens for. The
 * gate then re-runs /auth/status and re-renders the login screen
 * instead of empty app surfaces.
 *
 * The previous behaviour after a controller reinstall (or any session
 * expiry) was: SPA loads from PWA cache, every API call returns 401,
 * apps render empty with no signal to the user that they need to log
 * in again. Reported by jay after wiping his Pi data dir during the
 * install-server.sh re-test on 2026-05-08.
 *
 * Scope:
 * - Only triggers on /api/* paths so /auth/login itself returning 401
 *   for a bad password is handled by LoginGate's existing form flow.
 * - Throttled to one event per 2s so a burst of failed calls doesn't
 *   flood listeners.
 * - Idempotent install — calling installAuthGuard() twice is a no-op.
 */

import { withCsrf, getCsrfToken } from "./csrf";

const SESSION_EXPIRED_EVENT = "taos-session-expired";
const CSRF_MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

// Same-origin iff the resolved URL origin matches this page's. Parse the URL
// (not a string prefix, which a protocol-relative //evil.example or a lookalike
// host <origin>.evil.com would defeat, leaking the token).
function isSameOrigin(u: string): boolean {
  try {
    if (typeof window === "undefined" || !window.location) return false;
    return new URL(u, window.location.href).origin === window.location.origin;
  } catch {
    return false;
  }
}

// API prefixes whose 401s do NOT mean the controller session expired.
// /api/account/* proxies to the taos.my cloud account, a separate auth
// boundary: it returns 401 whenever the user is not signed into that cloud
// account, a normal state account-client maps to "signed-out". Firing
// session-expired on it flashed the login gate and bounced the user out of
// the Account pane (reported by jay 2026-07-01). Genuine controller-session
// expiry is still caught by every other /api/* call the desktop makes.
// (/auth/* is excluded implicitly by failing the /api/ test below.)
const SESSION_EXPIRED_EXCLUDE = [/\/api\/account\//];

let installed = false;

export function installAuthGuard(): void {
  if (installed) return;
  if (typeof window === "undefined" || typeof window.fetch !== "function") return;
  installed = true;

  const originalFetch = window.fetch.bind(window);
  let lastDispatch = 0;

  window.fetch = async function patchedFetch(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    // Attach the CSRF double-submit header to same-origin mutating requests so
    // the backend's router-wide verify_csrf gate is satisfied at every call
    // site, not only the handful that wrap withCsrf() by hand. Covers both
    // string/URL inputs (via withCsrf on init) and Request-object inputs (by
    // rebuilding the Request with the header). Gated to same-origin so the token
    // never leaks off-site; never overwrites an X-CSRF-Token a caller already set.
    let effectiveInput: RequestInfo | URL = input;
    let effectiveInit = init;
    if (typeof input === "string" || input instanceof URL) {
      if (isSameOrigin(input.toString())) effectiveInit = withCsrf(init);
    } else if (typeof Request !== "undefined" && input instanceof Request) {
      try {
        const method = (input.method || "GET").toUpperCase();
        if (
          CSRF_MUTATING.has(method) &&
          isSameOrigin(input.url) &&
          !input.headers.has("X-CSRF-Token")
        ) {
          const token = getCsrfToken();
          if (token) {
            const headers = new Headers(input.headers);
            headers.set("X-CSRF-Token", token);
            effectiveInput = new Request(input, { headers });
          }
        }
      } catch {
        effectiveInput = input;
      }
    }
    const response = await originalFetch(effectiveInput, effectiveInit);
    if (response.status === 401) {
      let url = "";
      if (typeof input === "string") url = input;
      else if (input instanceof URL) url = input.toString();
      else if (input && typeof (input as Request).url === "string") url = (input as Request).url;
      // Only react to API paths — auth endpoints handle their own 401s.
      // Match path-prefix so absolute URLs from the same origin work too.
      const isApi =
        /\/api\//.test(url) &&
        !SESSION_EXPIRED_EXCLUDE.some((re) => re.test(url));
      if (isApi) {
        const now = Date.now();
        if (now - lastDispatch > 2000) {
          lastDispatch = now;
          window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
        }
      }
    }
    return response;
  };
}

export { SESSION_EXPIRED_EVENT };
