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
const SHELL_BASE_URL = "http://127.0.0.1:6973";

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

export interface ShellWindow {
  con_id: number;
  app_id: string;
  title: string;
  label: string;
  workspace: string;
  focused: boolean;
  /** mtime (seconds) of the window's last snapshot; null or absent when it has none. */
  snapshot?: number | null;
}

/**
 * One request to taos-shelld, bounded by SHELL_LAUNCH_TIMEOUT_MS. Resolves the
 * parsed JSON body of a 2xx answer; undefined on a network error, a non-2xx, a
 * non-JSON body or no answer in time. Never rejects.
 */
async function shellRequest(path: string, body?: unknown): Promise<unknown> {
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<undefined>((resolve) => {
    timer = setTimeout(() => {
      ctrl?.abort();
      resolve(undefined);
    }, SHELL_LAUNCH_TIMEOUT_MS);
  });
  const attempt = (async () => {
    try {
      const res = await fetch(
        `${SHELL_BASE_URL}${path}`,
        body === undefined
          ? { signal: ctrl?.signal }
          : {
              method: "POST",
              headers: { "Content-Type": "application/json", "X-taOS-Shell": "1" },
              body: JSON.stringify(body),
              signal: ctrl?.signal,
            },
      );
      if (!res.ok) return undefined;
      return (await res.json()) as unknown;
    } catch {
      return undefined;
    }
  })();
  try {
    return await Promise.race([attempt, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

async function shellPostOk(path: string, body: unknown): Promise<boolean> {
  const res = await shellRequest(path, body);
  return !!res && typeof res === "object" && (res as { ok?: unknown }).ok === true;
}

/** Every native window taos-shelld lists (not the desktop itself); [] on any failure. */
export async function listShellWindows(): Promise<ShellWindow[]> {
  const res = await shellRequest("/windows");
  return Array.isArray(res) ? (res as ShellWindow[]) : [];
}

/**
 * Fetch a window's last snapshot as an object URL (blob:). The desktop CSP
 * img-src does not allow the shell origin, so an <img> must never point at it
 * directly. The caller owns the URL and must revoke it. null on a non-2xx, a
 * network error or no answer within SHELL_LAUNCH_TIMEOUT_MS. Never rejects.
 */
export async function fetchShellSnapshot(conId: number): Promise<string | null> {
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<null>((resolve) => {
    timer = setTimeout(() => {
      ctrl?.abort();
      resolve(null);
    }, SHELL_LAUNCH_TIMEOUT_MS);
  });
  const attempt = (async () => {
    try {
      const res = await fetch(`${SHELL_BASE_URL}/snapshot/${conId}`, { signal: ctrl?.signal });
      if (!res.ok) return null;
      const blob = await res.blob();
      // Lost the race to the timeout: nobody would revoke this URL.
      if (ctrl?.signal.aborted) return null;
      return URL.createObjectURL(blob);
    } catch {
      return null;
    }
  })();
  try {
    return await Promise.race([attempt, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

export function focusShellWindow(conId: number): Promise<boolean> {
  return shellPostOk("/focus", { con_id: conId });
}

export function closeShellWindow(conId: number): Promise<boolean> {
  return shellPostOk("/close", { con_id: conId });
}

/** Open an http(s) URL in the handset's Firefox; false when the shell did not. */
export function openUrlInShell(url: string): Promise<boolean> {
  return shellPostOk("/open-url", { url });
}

/** Test-only: forget the cached device class. */
export function _resetMobileShellForTests(): void {
  deviceClass = undefined;
  pending = null;
  attempts = 0;
}
