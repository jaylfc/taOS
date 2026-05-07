/**
 * React provider exposing the singleton BackendStatusController to the
 * tree via useBackendStatus(). The singleton is created once per page
 * load (one per browser tab) and started on mount.
 *
 * The taos-fetch wrapper consumes the same singleton so version
 * reports from any in-flight request reach the same status object.
 */
import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { createBackendStatus, type BackendStatusController, type BackendStatus } from "@/lib/backendStatus";
import { createTaosFetch } from "@/lib/taos-fetch";

let singleton: BackendStatusController | null = null;

export function getBackendStatusSingleton(): BackendStatusController {
  if (!singleton) {
    singleton = createBackendStatus({ healthUrl: "/api/health" });
  }
  return singleton;
}

// Module-level taos-fetch bound to the singleton — exposed for use
// outside React (e.g. plain modules that don't want to import a hook).
export const taosFetch = createTaosFetch({ status: getBackendStatusSingleton() });

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
    const refresh = () => setSnap({
      status: bs.getStatus(),
      currentVersion: bs.getCurrentVersion(),
      secondsReconnecting: bs.getSecondsReconnecting(),
    });
    const unsub = bs.subscribe(refresh);
    bs.start();
    // Light tick so secondsReconnecting updates the banner copy without
    // waiting for the next poll callback.
    const tick = setInterval(refresh, 1_000);
    return () => {
      unsub();
      clearInterval(tick);
    };
  }, [bs]);

  return <Ctx.Provider value={snap}>{children}</Ctx.Provider>;
}

export function useBackendStatus(): ContextValue {
  return useContext(Ctx);
}
