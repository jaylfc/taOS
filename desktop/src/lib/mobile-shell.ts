/**
 * taOSmobile shell launcher.
 *
 * On the taOSmobile handset the desktop runs in a Chromium kiosk under a Sway
 * compositor, and a loopback daemon (taos-shelld) can open an app as its own
 * compositor window: `app.html?app=<id>` in the kiosk's own browser profile,
 * so it is already signed in. This module is the one place that decides
 * whether an app open goes there, and performs the launch.
 *
 * The process store's openWindow is the choke point: it asks
 * `shouldLaunchInShell` synchronously and, when that says yes, calls
 * `launchInShell` and falls back to the in-page window when it resolves false.
 *
 * Nothing here ever throws or rejects: a dead or absent shell must leave the
 * desktop exactly as usable as before, via the in-page fallback.
 */
import { fetchTaosAgentConfig } from "@/lib/taos-agent-api";

export const SHELL_LAUNCH_URL = "http://127.0.0.1:6973/launch";
export const SHELL_LAUNCH_TIMEOUT_MS = 1500;

/**
 * System surfaces that stay in-page even on the handset. Settings mutates the
 * desktop shell's own state (theme, wallpaper, dock) live; opened as a separate
 * window, those changes would land in that window, not the desktop behind it.
 */
const IN_PAGE_APPS = new Set<string>(["settings"]);

// undefined: not fetched yet (or the last fetch failed); otherwise the value
// the server reported, which may be null.
let deviceClass: string | null | undefined;
let pending: Promise<void> | null = null;
let attempts = 0;
// A failed prime is retried from later opens, but only this many times in
// total, so a config endpoint that keeps failing never costs a fetch per open.
const MAX_PRIME_ATTEMPTS = 3;

/**
 * Fetch `device_class` from GET /api/taos-agent/config ONCE and cache it.
 * Call after auth is confirmed: an unauthenticated /api call trips the auth
 * guard's session-expired event. A failed fetch is not cached, so a later
 * prime (or the next open) retries; a successful one is never refetched.
 */
export function primeDeviceClass(): Promise<void> {
  if (deviceClass !== undefined) return Promise.resolve();
  if (pending) return pending;
  attempts += 1;
  pending = fetchTaosAgentConfig()
    .then((cfg) => {
      deviceClass = typeof cfg?.device_class === "string" ? cfg.device_class : null;
    })
    .catch(() => {
      deviceClass = undefined;
    })
    .finally(() => {
      pending = null;
    });
  return pending;
}

/** True only once the server has said this device is a taOSmobile handset. */
export function isShellDevice(): boolean {
  return deviceClass === "mobile";
}

/**
 * Whether an openWindow call should go to the handset shell. Synchronous: an
 * unknown device class answers false (in-page), so non-handset behaviour never
 * waits on a fetch.
 *
 * Opens that carry props (a deep link: a file path, a URL, a settings section)
 * or ask for a second window stay in-page: `app.html?app=<id>` cannot carry that
 * context, and the shell has no second-window concept.
 */
export function shouldLaunchInShell(
  appId: string,
  props?: Record<string, unknown>,
  opts?: { forceNew?: boolean },
): boolean {
  if (!isShellDevice()) {
    // Retry a prime that failed earlier (bounded); never start the first
    // fetch from here (that belongs to the authenticated app boot).
    if (attempts > 0 && attempts < MAX_PRIME_ATTEMPTS && deviceClass === undefined && !pending) {
      void primeDeviceClass();
    }
    return false;
  }
  if (IN_PAGE_APPS.has(appId)) return false;
  if (opts?.forceNew) return false;
  if (props && Object.keys(props).length > 0) return false;
  return true;
}

/**
 * Ask taos-shelld to open (or switch to) `appId` as its own window. Resolves
 * true only on a 2xx whose JSON body has `ok === true`; resolves false on a
 * network error, a non-2xx, a non-JSON body, `ok` not true, or no answer
 * within SHELL_LAUNCH_TIMEOUT_MS.
 */
export async function launchInShell(appId: string): Promise<boolean> {
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<false>((resolve) => {
    timer = setTimeout(() => {
      ctrl?.abort();
      resolve(false);
    }, SHELL_LAUNCH_TIMEOUT_MS);
  });
  const attempt = (async () => {
    try {
      const res = await fetch(SHELL_LAUNCH_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-taOS-Shell": "1" },
        body: JSON.stringify({ app: appId }),
        signal: ctrl?.signal,
      });
      if (!res.ok) return false;
      const body: unknown = await res.json();
      return !!body && typeof body === "object" && (body as { ok?: unknown }).ok === true;
    } catch {
      return false;
    }
  })();
  try {
    return await Promise.race([attempt, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

/** Test-only: forget the cached device class. */
export function _resetMobileShellForTests(): void {
  deviceClass = undefined;
  pending = null;
  attempts = 0;
}
