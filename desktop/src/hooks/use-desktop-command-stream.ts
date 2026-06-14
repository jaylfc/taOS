import { useEffect } from "react";

/**
 * Subscribes to the controller's desktop-command SSE channel and re-dispatches
 * each command as the CustomEvents the desktop already understands:
 *
 *   { kind: "open-app", payload: { app, props } } -> `taos:open-app`
 *   { kind: "window",   payload: WindowOp }       -> `taos:window`
 *
 * This is the browser end of the agent-OS-control transport: the taOS agent
 * pushes commands server-side (POST /api/desktop/command), the controller fans
 * them to the user's desktop over /api/desktop/stream, and they land on the
 * existing use-deep-navigation / use-desktop-control receivers. The browser
 * auto-reconnects on transient errors (same as the canvas stream).
 */
export function useDesktopCommandStream(): void {
  useEffect(() => {
    const es = new EventSource("/api/desktop/stream");
    es.onmessage = (msg) => {
      let cmd: { kind?: string; payload?: Record<string, unknown> };
      try {
        cmd = JSON.parse(msg.data);
      } catch {
        return;
      }
      if (cmd.kind === "open-app") {
        window.dispatchEvent(new CustomEvent("taos:open-app", { detail: cmd.payload ?? {} }));
      } else if (cmd.kind === "window") {
        window.dispatchEvent(new CustomEvent("taos:window", { detail: cmd.payload ?? {} }));
      }
    };
    es.onerror = () => {
      // Transient errors: the browser reconnects automatically. On a hard close
      // the effect's cleanup runs and a remount opens a fresh subscription.
    };
    return () => es.close();
  }, []);
}
