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
    // Only poll while the tab is visible; a backgrounded tab does not need to
    // keep hitting the endpoint, and it resyncs the moment it returns.
    let interval: ReturnType<typeof setInterval> | null = null;
    const startPolling = () => {
      if (interval === null) interval = setInterval(() => void sync(), POLL_MS);
    };
    const stopPolling = () => {
      if (interval !== null) {
        clearInterval(interval);
        interval = null;
      }
    };
    const onVisibility = () => {
      if (document.hidden) {
        stopPolling();
      } else {
        void sync();
        startPolling();
      }
    };
    if (!document.hidden) startPolling();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      stopPolling();
      document.removeEventListener("visibilitychange", onVisibility);
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
