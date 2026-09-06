import { useCallback, useEffect, useState } from "react";
import { onAppEvent, APP_OPTIONAL_CHANGED } from "@/lib/app-event-bus";

/**
 * Tracks which optional frontend apps (Reddit / YouTube / GitHub / X) the user
 * has installed from the Store. Returns the set of installed app ids.
 *
 * Re-fetches whenever an APP_OPTIONAL_CHANGED event fires (Store install /
 * remove), so the launchpad, search palette, and mobile home surface or hide
 * the app immediately without a reload.
 *
 * The set is empty while loading or on error — optional apps stay hidden, which
 * is the correct default (they are opt-in).
 */
export function useInstalledOptionalApps(): Set<string> {
  const [installed, setInstalled] = useState<Set<string>>(() => new Set());

  const fetchInstalled = useCallback(() => {
    let cancelled = false;
    fetch("/api/apps/optional/installed", { headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : { installed: [] }))
      .then((data: { installed?: string[] }) => {
        if (!cancelled) setInstalled(new Set(data.installed ?? []));
      })
      .catch(() => {
        // Leave the set as-is on error; optional apps simply stay hidden.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => fetchInstalled(), [fetchInstalled]);

  useEffect(
    () => onAppEvent(APP_OPTIONAL_CHANGED, () => fetchInstalled()),
    [fetchInstalled],
  );

  return installed;
}
