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
  const subscribers = new Set<() => void>();

  const notify = () => subscribers.forEach((cb) => cb());

  const setStatus = (s: BackendStatus) => {
    if (status === s) return;
    status = s;
    if (s === "reconnecting" && reconnectingSince === null) {
      reconnectingSince = Date.now();
    } else if (s === "up") {
      reconnectingSince = null;
      attemptIndex = 0;
    }
    notify();
  };

  const nextDelay = () => {
    const i = Math.min(attemptIndex, POLL_DELAYS_MS.length - 1);
    return POLL_DELAYS_MS[i];
  };

  const schedule = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(poll, nextDelay());
  };

  const poll = async () => {
    try {
      const r = await fetchImpl(opts.healthUrl, { credentials: "include" });
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
      // Long-reconnecting subscribers (banner copy switch) need a notify
      // shortly after the threshold even if status didn't change.
      if (status === "reconnecting" && reconnectingSince !== null) {
        const elapsed = Date.now() - reconnectingSince;
        if (elapsed >= LONG_RECONNECTING_MS) notify();
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
    subscribe(cb) {
      subscribers.add(cb);
      return () => subscribers.delete(cb);
    },
    start() {
      if (timer) return;
      schedule();
    },
    stop() {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    },
  };
}
