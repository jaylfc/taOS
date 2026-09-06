/**
 * Polls the deployed SPA version manifest and returns whether a newer build
 * is available than the one currently running.
 *
 * The build step writes `/desktop/version.json` with the full
 * __TAOS_VERSION__ (`{pkg}+{sha}.{ts}`) so the running SPA can detect
 * when a redeploy has landed — even when the backend package version
 * hasn't changed.  Compare this with useUpdateAvailable(), which watches
 * for backend-version bumps via the X-Taos-Version response header.
 *
 * Polling:
 *  - on window focus (user returns to the tab)
 *  - every 60 s while the tab is visible
 *
 * A dev-style build version (starts with "dev" or "0.0.0") is never
 * compared — the hook stays silent in local development.
 *
 * Guards:
 *  - fires at most once per unique deployed version (persisted via useRef)
 *  - `no-store` fetch mode prevents browser HTTP caching of version.json
 */
import { useState, useEffect, useRef, useCallback } from "react";

declare const __TAOS_VERSION__: string | undefined;

function getBuildVersion(): string {
  return typeof __TAOS_VERSION__ === "string" ? __TAOS_VERSION__ : "dev";
}
const DEV_VERSION_PATTERN = /^(dev|0\.0\.0)/i;
const POLL_INTERVAL_MS = 60_000;
const VERSION_URL = "/desktop/version.json";

interface VersionPayload {
  version: string;
}

export function useSpaVersionCheck(): {
  hasNewBuild: boolean;
  deployedVersion: string | null;
} {
  const [deployedVersion, setDeployedVersion] = useState<string | null>(null);
  const mountedRef = useRef(true);

  const isDev = DEV_VERSION_PATTERN.test(getBuildVersion());

  const check = useCallback(async () => {
    if (isDev) return;
    try {
      const r = await fetch(VERSION_URL, { cache: "no-store" });
      if (!r.ok) return;
      const payload = (await r.json()) as VersionPayload;
      const deployed = payload?.version;
      if (!deployed || typeof deployed !== "string") return;
      if (!mountedRef.current) return;

      setDeployedVersion((prev) => (prev === deployed ? prev : deployed));
    } catch {
      // version.json unreachable — backend may be restarting; the
      // BackendStatusProvider already surfaces that state.
    }
  }, [isDev]);

  // Initial check on mount.
  useEffect(() => {
    mountedRef.current = true;
    if (!isDev) check();
    return () => {
      mountedRef.current = false;
    };
  }, [check, isDev]);

  // Periodic poll while visible.
  useEffect(() => {
    if (isDev) return;
    let interval: ReturnType<typeof setInterval> | null = null;

    const startPolling = () => {
      if (interval) return;
      check();
      interval = setInterval(check, POLL_INTERVAL_MS);
    };

    const stopPolling = () => {
      if (interval) {
        clearInterval(interval);
        interval = null;
      }
    };

    const onVisible = () => {
      if (document.visibilityState === "visible") {
        startPolling();
      } else {
        stopPolling();
      }
    };

    onVisible();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopPolling();
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [check, isDev]);

  // Check on window focus.
  useEffect(() => {
    if (isDev) return;
    const onFocus = () => check();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [check, isDev]);

  const hasNewBuild =
    !isDev &&
    deployedVersion !== null &&
    deployedVersion !== getBuildVersion();

  return { hasNewBuild, deployedVersion };
}
