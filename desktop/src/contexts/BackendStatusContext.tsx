/**
 * React provider exposing the singleton BackendStatusController to the
 * tree via useBackendStatus(). The singleton is created once per page
 * load (one per browser tab) and started on mount.
 */
import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { createBackendStatus, type BackendStatusController, type BackendStatus } from "@/lib/backendStatus";

let singleton: BackendStatusController | null = null;

export function getBackendStatusSingleton(): BackendStatusController {
  if (!singleton) {
    singleton = createBackendStatus({ healthUrl: "/api/health" });
  }
  return singleton;
}

interface ContextValue {
  status: BackendStatus;
  currentVersion: string | null;
  secondsReconnecting: number;
}

const Ctx = createContext<ContextValue>({
  status: "up",
  currentVersion: null,
  secondsReconnecting: 0,
});

export function BackendStatusProvider({ children }: { children: ReactNode }) {
  const bs = getBackendStatusSingleton();
  const [snap, setSnap] = useState<ContextValue>(() => ({
    status: bs.getStatus(),
    currentVersion: bs.getCurrentVersion(),
    secondsReconnecting: bs.getSecondsReconnecting(),
  }));

  useEffect(() => {
    const refresh = () => setSnap((prev) => {
      const next = {
        status: bs.getStatus(),
        currentVersion: bs.getCurrentVersion(),
        secondsReconnecting: bs.getSecondsReconnecting(),
      };
      if (
        prev.status === next.status &&
        prev.currentVersion === next.currentVersion &&
        prev.secondsReconnecting === next.secondsReconnecting
      ) {
        return prev;
      }
      return next;
    });
    const unsub = bs.subscribe(refresh);
    bs.start();
    return () => {
      unsub();
      // Intentionally do NOT call bs.stop() — the controller is a page-lifetime
      // singleton; another Provider mount may need it still polling.
    };
  }, [bs]);

  // Separate effect: only run the 1s tick while reconnecting, so steady-state
  // "up" doesn't fire a function every second forever.
  useEffect(() => {
    if (snap.status !== "reconnecting") return;
    const tick = setInterval(() => {
      setSnap((prev) => {
        const nextSecs = bs.getSecondsReconnecting();
        if (prev.secondsReconnecting === nextSecs) return prev;
        return { ...prev, secondsReconnecting: nextSecs };
      });
    }, 1_000);
    return () => clearInterval(tick);
  }, [snap.status, bs]);

  return <Ctx.Provider value={snap}>{children}</Ctx.Provider>;
}

export function useBackendStatus(): ContextValue {
  return useContext(Ctx);
}
