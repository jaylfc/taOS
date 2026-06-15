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
/** Event the ScreenshotFlash overlay listens for to play the capture effect. */
export const SCREENSHOT_FLASH_EVENT = "taos:screenshot-flash";

/**
 * Rasterise the live desktop and POST it back to resolve an agent screenshot
 * request. Plays a subtle flash effect around the capture. DOM rasterisation
 * cannot read cross-origin iframes (the Browser's proxied page), so those
 * appear blank; the desktop chrome and native apps capture fully.
 */
async function captureAndReport(requestId: string): Promise<void> {
  let body: { request_id: string; image?: string; error?: string };
  try {
    // Prefer a live screen-capture grant (full fidelity incl. cross-origin
    // iframes like the Browser's proxied page); fall back to DOM rasterisation
    // (chrome + native apps only) when no grant is active.
    const { hasScreenCapture, grabScreenFrame } = await import("@/lib/screen-capture");
    let dataUrl: string | null = null;
    if (hasScreenCapture()) {
      dataUrl = await grabScreenFrame();
    }
    if (!dataUrl) {
      const { domToPng } = await import("modern-screenshot");
      // Full viewport: top bar + desktop + dock.
      dataUrl = await domToPng(document.body, {
        backgroundColor: getComputedStyle(document.body).backgroundColor || "#000",
        // Skip the capture overlay/flash node itself.
        filter: (node) =>
          !(node instanceof HTMLElement && node.dataset.screenshotExclude === "true"),
      });
    }
    body = { request_id: requestId, image: dataUrl };
  } catch (e) {
    body = { request_id: requestId, error: e instanceof Error ? e.message : "capture failed" };
  }
  // Flash AFTER the frame is captured so the white veil never leaks into a
  // full-fidelity getDisplayMedia frame (that path captures the real composited
  // screen, where an on-screen overlay is not excludable like the DOM-raster
  // filter is). Still reads as a shutter: capture is sub-second.
  window.dispatchEvent(new CustomEvent(SCREENSHOT_FLASH_EVENT));
  try {
    await fetch("/api/desktop/screenshot-result", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    /* the agent side will time out and report it */
  }
}

export function useDesktopCommandStream(): void {
  useEffect(() => {
    const es = new EventSource("/api/desktop/stream");
    es.onmessage = (msg) => {
      let cmd: { kind?: string; payload?: Record<string, unknown> } | null;
      try {
        cmd = JSON.parse(msg.data);
      } catch {
        return;
      }
      // JSON.parse can legally return null (or a non-object); guard before
      // reading .kind so a stray payload can't throw and kill the listener.
      if (!cmd || typeof cmd !== "object") return;
      if (cmd.kind === "open-app") {
        window.dispatchEvent(new CustomEvent("taos:open-app", { detail: cmd.payload ?? {} }));
      } else if (cmd.kind === "window") {
        window.dispatchEvent(new CustomEvent("taos:window", { detail: cmd.payload ?? {} }));
      } else if (cmd.kind === "screenshot") {
        const requestId = (cmd.payload?.request_id as string) ?? "";
        if (requestId) void captureAndReport(requestId);
      }
    };
    es.onerror = () => {
      // Transient errors: the browser reconnects automatically. On a hard close
      // the effect's cleanup runs and a remount opens a fresh subscription.
    };
    return () => es.close();
  }, []);
}
