/**
 * Renders nothing visually — uses the existing global notification store
 * to push a single transient toast when the backend reports a different
 * version than the one this SPA was built with.
 *
 * Skipped entirely in dev builds (build version starts with "dev" or
 * matches "0.0.0-...") so local hacking doesn't trigger spam.
 *
 * Dismissable by the user; doesn't reappear in this session unless the
 * version changes again. Reload picks up the new build naturally and
 * clears the mismatch.
 *
 * Note: the notification store uses `body` (not `message`) and `action`
 * is a string URL — there is no actions[] callback API. Reload
 * instructions are surfaced in the body text instead.
 */
import { useEffect, useRef } from "react";
import { useUpdateAvailable } from "@/hooks/use-update-available";
import { useBackendStatus } from "@/contexts/BackendStatusContext";
import { useNotificationStore } from "@/stores/notification-store";

interface Props {
  buildVersion: string;
}

export function UpdateAvailableToast({ buildVersion }: Props) {
  const { currentVersion } = useBackendStatus();
  const hasUpdate = useUpdateAvailable(buildVersion);
  const addNotification = useNotificationStore((s) => s.addNotification);
  const firedFor = useRef<string | null>(null);

  useEffect(() => {
    if (!hasUpdate) return;
    if (!currentVersion) return;
    if (firedFor.current === currentVersion) return;
    firedFor.current = currentVersion;
    addNotification({
      source: "system",
      level: "info",
      title: "New taOS version available",
      body: `Reload to upgrade from ${buildVersion} to ${currentVersion}.`,
    });
  }, [hasUpdate, buildVersion, currentVersion, addNotification]);

  return null;
}
