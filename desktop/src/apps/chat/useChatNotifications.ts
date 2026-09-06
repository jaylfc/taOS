import { useCallback, useRef } from "react";

/**
 * Browser notifications for messages in channels the user is not viewing.
 * Permission is requested lazily on the first notify, never on load. No
 * notification fires while the window is focused (the in-app UI is enough).
 */
export function useChatNotifications() {
  const asked = useRef(false);

  const ensurePermission = useCallback(async (): Promise<boolean> => {
    if (typeof Notification === "undefined") return false;
    if (Notification.permission === "granted") return true;
    if (Notification.permission === "denied" || asked.current) return false;
    asked.current = true;
    try { return (await Notification.requestPermission()) === "granted"; }
    catch { return false; }
  }, []);

  const notify = useCallback(async (title: string, body: string, onClick: () => void) => {
    if (!(await ensurePermission())) return;
    if (document.hasFocus()) return; // window focused: in-app UI is enough
    try {
      const n = new Notification(title, { body: body.slice(0, 140) });
      n.onclick = () => { window.focus(); onClick(); n.close(); };
    } catch { /* notification constructor can throw on some platforms */ }
  }, [ensurePermission]);

  return { notify, ensurePermission };
}
