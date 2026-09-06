/**
 * Backend status state machine.
 *
 * Polls /api/health on a backoff timer. Exposes status, current backend
 * version, and elapsed reconnecting time. Pure module — no React, no DOM.
 *
 * Used by BackendStatusContext (React provider) and taos-fetch (which
 * reports versions seen in response headers).
 */

export type BackendStatus = "up" | "reconnecting" | "down";

const VERSION_PATTERN = /^[\w.+\-]+$/;
const POLL_DELAYS_MS = [2_000, 4_000, 8_000, 16_000, 30_000];
const LONG_RECONNECTING_MS = 60_000;

interface Options {
  healthUrl: string;
  fetchImpl?: typeof fetch;
}

export interface BackendStatusController {
  getStatus(): BackendStatus;
  getCurrentVersion(): string | null;
  getSecondsReconnecting(): number;
  reportVersion(v: string): void;
  subscribe(cb: () => void): () => void;
  start(): void;
  stop(): void;
}

export function createBackendStatus(opts: Options): BackendStatusController {
  const fetchImpl = opts.fetchImpl ?? fetch;
  let status: BackendStatus = "up";
  let currentVersion: string | null = null;
  let reconnectingSince: number | null = null;
  let attemptIndex = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  let longReconnectingNotified = false;
  const subscribers = new Set<() => void>();

  // Bug 2 fix: wrap each subscriber in try/catch so a throwing subscriber
  // does not poison sibling subscribers or the surrounding poll logic.
  const notify = () => subscribers.forEach((cb) => {
    try { cb(); } catch (err) { console.warn("[backendStatus] subscriber threw:", err); }
  });

  const setStatus = (s: BackendStatus) => {
    if (status === s) return;
    status = s;
    if (s === "reconnecting" && reconnectingSince === null) {
      reconnectingSince = Date.now();
    } else if (s === "up") {
      reconnectingSince = null;
      attemptIndex = 0;
      // Bug 3 fix: reset the latch when we leave reconnecting.
      longReconnectingNotified = false;
    }
    notify();
  };

  const nextDelay = () => {
    const i = Math.min(attemptIndex, POLL_DELAYS_MS.length - 1);
    return POLL_DELAYS_MS[i];
  };

  // Bug 1 fix: guard schedule() with stopped flag so a mid-flight poll's
  // finally clause cannot re-arm the timer after stop() was called.
  const schedule = () => {
    if (stopped) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(poll, nextDelay());
  };

  const poll = async () => {
    try {
      // Bug 4 fix: 5-second fetch timeout so a hung backend doesn't wedge.
      const r = await fetchImpl(opts.healthUrl, {
        credentials: "include",
        signal: AbortSignal.timeout(5000),
      });
      if (r.ok) {
        const v = r.headers.get("X-Taos-Version");
        if (v && VERSION_PATTERN.test(v)) {
          if (v !== currentVersion) {
            currentVersion = v;
            notify();
          }
        }
        setStatus("up");
        attemptIndex = 0;
      } else {
        setStatus("reconnecting");
        attemptIndex += 1;
      }
    } catch {
      setStatus("reconnecting");
      attemptIndex += 1;
    } finally {
      // Bug 3 fix: notify exactly once when crossing the 60s threshold.
      if (status === "reconnecting" && reconnectingSince !== null && !longReconnectingNotified) {
        const elapsed = Date.now() - reconnectingSince;
        if (elapsed >= LONG_RECONNECTING_MS) {
          longReconnectingNotified = true;
          notify();
        }
      }
      schedule();
    }
  };

  return {
    getStatus: () => status,
    getCurrentVersion: () => currentVersion,
    getSecondsReconnecting: () =>
      reconnectingSince === null ? 0 : Math.floor((Date.now() - reconnectingSince) / 1000),
    reportVersion(v: string) {
      if (!v || !VERSION_PATTERN.test(v)) return;
      if (v !== currentVersion) {
        currentVersion = v;
        notify();
      }
    },
    // Cleanup #5: wrap the delete return value explicitly.
    subscribe(cb) {
      subscribers.add(cb);
      return () => { subscribers.delete(cb); };
    },
    start() {
      // Bug 1 fix: reset stopped so schedule() is allowed to arm the timer.
      stopped = false;
      if (timer) return;
      schedule();
    },
    stop() {
      // Bug 1 fix: set stopped before clearing the timer so a mid-flight
      // poll's finally clause sees it and skips re-arming.
      stopped = true;
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    },
  };
}
