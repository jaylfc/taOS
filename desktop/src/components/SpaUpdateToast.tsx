/**
 * Shows a transient notification when the deployed SPA version differs
 * from the one currently running — i.e. a new build has been deployed
 * and a page reload will pick it up.
 *
 * Distinct from UpdateAvailableToast which watches for backend-version
 * bumps via the X-Taos-Version response header.  This component detects
 * SPA-only redeploys (settings-triggered rebuilds, UI hotfixes) where
 * the backend package version hasn't changed.
 *
 * Silent in dev builds.  Fires at most once per unique deployed version.
 * The user dismisses the toast; a reload is not forced automatically to
 * avoid disrupting active sessions (mid-typing, etc.).
 */
import { useEffect, useRef } from "react";
import { useSpaVersionCheck } from "@/hooks/use-spa-version-check";
import { useNotificationStore } from "@/stores/notification-store";

declare const __TAOS_VERSION__: string | undefined;
const BUILD_VERSION = typeof __TAOS_VERSION__ === "string" ? __TAOS_VERSION__ : "dev";

export function SpaUpdateToast() {
  const { hasNewBuild, deployedVersion } = useSpaVersionCheck();
  const addNotification = useNotificationStore((s) => s.addNotification);
  const firedFor = useRef<string | null>(null);

  useEffect(() => {
    if (!hasNewBuild) return;
    if (!deployedVersion) return;
    if (firedFor.current === deployedVersion) return;
    firedFor.current = deployedVersion;

    addNotification({
      source: "system",
      level: "info",
      title: "New taOS build available",
      body: `Reload to update from ${BUILD_VERSION} to ${deployedVersion}.`,
    });
  }, [hasNewBuild, deployedVersion, addNotification]);

  return null;
}
