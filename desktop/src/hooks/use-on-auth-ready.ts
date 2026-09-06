import { useEffect, useRef } from "react";
import { useAuthReadyStore } from "@/stores/auth-ready-store";

// Runs `callback` once per authenticated session: the first time
// auth-ready flips true, and again after it drops back to false and flips
// true a second time (i.e. a subsequent login). Does NOT run while logged
// out, and does not re-run on every render while still logged in.
//
// Extracted so session-scoped restore effects (active-theme, desktop
// settings) that live outside LoginGate's children can share the same fix
// for #1601/#1603: those effects used to run unconditionally on mount,
// before LoginGate's /auth/status check resolves, so they either 401'd
// against a logged-out session or read stale data — and, having already
// "run" once, were never retried after the user actually logged in.
export function useOnAuthReady(callback: () => void) {
  const ranForThisSession = useRef(false);
  const authReady = useAuthReadyStore((s) => s.ready);

  useEffect(() => {
    if (!authReady) {
      ranForThisSession.current = false;
      return;
    }
    if (ranForThisSession.current) return;
    ranForThisSession.current = true;
    callback();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authReady]);
}
