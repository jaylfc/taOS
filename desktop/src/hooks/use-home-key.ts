import { useEffect } from "react";

/**
 * The key the handset's nav bar sends for home: taOSmobile's `taos-shell home`
 * switches to the taOS workspace, then types F13. Nothing else on the device
 * binds F13, so it cannot collide with a browser or app shortcut.
 */
export const HOME_KEY = "F13";

/** Runs `onHome` when the home key arrives, while `enabled` (mobile only). */
export function useHomeKey(enabled: boolean, onHome: () => void): void {
  useEffect(() => {
    if (!enabled) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key !== HOME_KEY) return;
      e.preventDefault();
      onHome();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [enabled, onHome]);
}
