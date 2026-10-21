import { useEffect } from "react";

/**
 * The key the handset's nav bar sends for the app switcher: taOSmobile's
 * `taos-shell switcher` switches to the taOS workspace, then types F14. Like
 * F13 (home), nothing else on the device binds it.
 */
export const SWITCHER_KEY = "F14";

/** Runs `onSwitcher` when the switcher key arrives, while `enabled` (mobile only). */
export function useSwitcherKey(enabled: boolean, onSwitcher: () => void): void {
  useEffect(() => {
    if (!enabled) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key !== SWITCHER_KEY) return;
      e.preventDefault();
      onSwitcher();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [enabled, onSwitcher]);
}
