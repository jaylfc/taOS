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

const SESSION_EXPIRED_EVENT = "taos-session-expired";
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
    const response = await originalFetch(input, init);
    if (response.status === 401) {
      let url = "";
      if (typeof input === "string") url = input;
      else if (input instanceof URL) url = input.toString();
      else if (input && typeof (input as Request).url === "string") url = (input as Request).url;
      // Only react to API paths — auth endpoints handle their own 401s.
      // Match path-prefix so absolute URLs from the same origin work too.
      const isApi = /\/api\//.test(url);
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
