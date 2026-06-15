/**
 * Resolves the browser-proxy origin for the BrowserApp.
 *
 * The proxy is served on a SECOND, API-free origin (a separate port) so the
 * proxied iframe can take allow-same-origin and run a service worker without
 * that SW being able to reach taOS APIs. The frontend learns the proxy port
 * from the public /api/desktop/browser/proxy-config probe, then builds the
 * origin from the CURRENT access host so it works over LAN / Tailscale /
 * taos.local.
 *
 * Single-port fallback: when the backend reports port 0 (or the proxy port
 * equals the main port), there is no separate origin — proxy URLs stay on the
 * current origin and the old same-origin behaviour applies.
 */

let _cachedPort: number | null = null;

/** Fetch the proxy port from the backend (cached for the session). 0 = single-port. */
export async function getBrowserProxyPort(): Promise<number> {
  if (_cachedPort !== null) return _cachedPort;
  try {
    const resp = await fetch("/api/desktop/browser/proxy-config", {
      credentials: "include",
    });
    if (!resp.ok) {
      _cachedPort = 0;
      return 0;
    }
    const body = await resp.json();
    const port = Number(body?.port);
    _cachedPort = Number.isFinite(port) && port > 0 ? port : 0;
    return _cachedPort;
  } catch {
    _cachedPort = 0;
    return 0;
  }
}

/**
 * Build the proxy origin from the current access host + the proxy port.
 * Returns the current origin (same-origin) when in single-port mode so the
 * caller transparently degrades to the historical behaviour.
 */
export function buildProxyOrigin(port: number): string {
  if (!port || String(port) === window.location.port) {
    return window.location.origin;
  }
  return `${window.location.protocol}//${window.location.hostname}:${port}`;
}

/** Resolve the proxy origin string in one call. */
export async function getBrowserProxyOrigin(): Promise<string> {
  const port = await getBrowserProxyPort();
  return buildProxyOrigin(port);
}

/** Test-only: reset the cached port. */
export function __resetProxyConfigCache(): void {
  _cachedPort = null;
}

/**
 * Mint a single-use, short-lived (30s) ticket for the proxy origin's redeem
 * flow. Authed, same-origin (:6969). Returns the token, or null on failure.
 */
export async function mintProxyTicket(): Promise<string | null> {
  try {
    const resp = await fetch("/api/desktop/browser/proxy-ticket", {
      method: "POST",
      credentials: "include",
    });
    if (!resp.ok) return null;
    const body = await resp.json();
    return typeof body?.ticket === "string" ? body.ticket : null;
  } catch {
    return null;
  }
}

/**
 * Build the proxied on-origin path (same params the SW + rewriter use).
 * Returns "" for blank/about: URLs (the caller should render about:blank).
 */
export function buildProxiedPath(
  profileId: string,
  url: string,
  tabId: string,
): string {
  if (!url || url === "about:blank" || url.startsWith("about:")) {
    return "";
  }
  const params = new URLSearchParams({
    profile_id: profileId,
    url,
    tab_id: tabId,
  });
  return `/api/desktop/browser/proxy?${params.toString()}`;
}

/**
 * Build the cross-origin redeem URL the iframe loads. The redeem endpoint on
 * the proxy origin validates the ticket, sets the taos_browser cookie, then
 * 302s to `next` (the proxied path). In single-port mode `proxyOrigin` is the
 * current origin, so this is effectively a same-origin redeem.
 */
export function buildRedeemUrl(
  proxyOrigin: string,
  ticket: string,
  proxiedPath: string,
  colorScheme?: "light" | "dark",
): string {
  const params = new URLSearchParams({
    ticket,
    next: proxiedPath,
  });
  // Carry the taOS colour scheme so proxied sites that support dark/light
  // render to match the shell (redeem sets a taos_cs cookie on the proxy origin).
  if (colorScheme === "light" || colorScheme === "dark") {
    params.set("cs", colorScheme);
  }
  return `${proxyOrigin}/__taos/redeem?${params.toString()}`;
}
