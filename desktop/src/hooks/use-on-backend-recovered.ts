import { useEffect, useRef } from "react";
import { useBackendStatus } from "@/contexts/BackendStatusContext";

/**
 * Calls `callback` each time the backend recovers (transitions from
 * reconnecting to up). The callback runs with no arguments.
 *
 * This is a lightweight alternative to listening for the `taos:backend-up`
 * window event directly — it uses the `recoveredAt` counter exposed by
 * `BackendStatusProvider`, so it works inside React's render cycle and
 * plays nicely with Suspense / concurrent features.
 */
export function useOnBackendRecovered(callback: () => void | Promise<void>): void {
  const { recoveredAt } = useBackendStatus();
  const callbackRef = useRef(callback);
  const prevRecoveredAtRef = useRef(recoveredAt);
  callbackRef.current = callback;

  useEffect(() => {
    if (recoveredAt > prevRecoveredAtRef.current) {
      callbackRef.current();
    }
    prevRecoveredAtRef.current = recoveredAt;
  }, [recoveredAt]);
}