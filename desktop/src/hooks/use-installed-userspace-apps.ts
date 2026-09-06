import { useState, useEffect, useCallback } from "react";
import type { AppManifest } from "@/registry/app-registry";
import { syncUserspaceApps } from "@/registry/app-registry";
import { fetchUserspaceApps, USERSPACE_APPS_CHANGED } from "@/lib/userspace-apps";
import { onAppEvent } from "@/lib/app-event-bus";

/**
 * Fetches installed userspace (.taosapp) packages from /api/userspace-apps,
 * syncs them into the app registry, and returns the manifest list.
 * Re-fetches automatically when a USERSPACE_APPS_CHANGED event fires on the
 * shared EventBus (e.g. after a successful Store install or uninstall).
 * Returns an empty list while loading or on error.
 */
export function useInstalledUserspaceApps(): AppManifest[] {
  const [apps, setApps] = useState<AppManifest[]>([]);

  const refresh = useCallback(() => {
    let cancelled = false;
    fetchUserspaceApps()
      .then((list) => {
        if (!cancelled) {
          syncUserspaceApps(list);
          setApps(list);
        }
      })
      .catch(() => {
        // Silently ignore -- Apps section just won't appear
      });
    return () => { cancelled = true; };
  }, []);

  // Initial fetch
  useEffect(() => {
    return refresh();
  }, [refresh]);

  // Re-fetch when a userspace app is installed, updated, or removed.
  // Cancel any in-flight fetch before starting a new one (and on unmount) so a
  // slower, older response cannot overwrite newer data or setState after unmount.
  useEffect(() => {
    let cancel: () => void = () => {};
    const off = onAppEvent(USERSPACE_APPS_CHANGED, () => {
      cancel();
      cancel = refresh();
    });
    return () => {
      cancel();
      off();
    };
  }, [refresh]);

  return apps;
}
