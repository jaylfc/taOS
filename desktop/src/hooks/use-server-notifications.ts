import { useEffect } from "react";
import { useNotificationStore } from "@/stores/notification-store";
import { fetchServerNotifications } from "@/lib/server-notifications";

const POLL_MS = 30_000;

/**
 * Keep the notification store in sync with the backend feed: fetch + merge on
 * mount, poll every 30s, and refresh immediately whenever the notification
 * centre transitions to open. Mount once under the desktop shell.
 */
export function useServerNotifications() {
  const centreOpen = useNotificationStore((s) => s.centreOpen);

  // Mount: initial sync + polling loop.
  useEffect(() => {
    if (typeof window === "undefined") return;

    let cancelled = false;
    const sync = async () => {
      const items = await fetchServerNotifications();
      if (!cancelled) useNotificationStore.getState().mergeServerNotifications(items);
    };

    void sync();
    const interval = setInterval(() => void sync(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  // Refresh on open so the bell shows the latest without waiting for the poll.
  useEffect(() => {
    if (typeof window === "undefined" || !centreOpen) return;
    let cancelled = false;
    void fetchServerNotifications().then((items) => {
      if (!cancelled) useNotificationStore.getState().mergeServerNotifications(items);
    });
    return () => {
      cancelled = true;
    };
  }, [centreOpen]);
}
