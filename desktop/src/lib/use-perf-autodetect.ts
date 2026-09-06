import { useEffect } from "react";
import { useThemeStore } from "@/stores/theme-store";

const KEY = "taos-reduce-effects";
// Frames-per-second below this on first load means the GPU is struggling with
// the full effects, so we turn them off automatically. Comfortably below 60 so
// a healthy machine never trips it, but high enough to catch a laggy low-end GPU.
const FPS_THRESHOLD = 40;
const PROBE_MS = 1000;

/**
 * First-run performance auto-detect (#58). When the user has made no explicit
 * Reduce-effects choice yet, measure the frame rate for ~1s and enable Reduce
 * effects if the device is struggling, so low-end hardware is smooth out of the
 * box. An explicit choice (on/off) is always honored and never overridden; a
 * capable machine is left untouched (and simply re-probed on the next load).
 */
export function usePerfAutoDetect(): void {
  const setReduceEffects = useThemeStore((s) => s.setReduceEffects);

  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(KEY);
    } catch {
      // localStorage unavailable: skip the probe, leave effects on.
      return;
    }
    if (stored === "on" || stored === "off") return; // user already chose

    if (typeof performance === "undefined" || typeof requestAnimationFrame === "undefined") {
      return;
    }

    let frames = 0;
    let raf = 0;
    // A backgrounded tab throttles requestAnimationFrame to ~1fps, which would
    // look exactly like a struggling GPU. Only trust the probe if the tab stayed
    // visible the whole time: bail if it starts hidden, and drop the result if
    // it is hidden at any point during the probe.
    const hidden = () => typeof document !== "undefined" && document.hidden;
    if (hidden()) return;
    let trustworthy = true;
    const onVis = () => {
      if (hidden()) trustworthy = false;
    };
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVis);
    }
    const start = performance.now();
    const tick = (now: number) => {
      frames += 1;
      if (now - start < PROBE_MS) {
        raf = requestAnimationFrame(tick);
        return;
      }
      const fps = (frames * 1000) / (now - start);
      // A low-end device benefits from Reduce effects; enabling it persists the
      // choice as "on", which the no-flash boot script reads on the next load. A
      // capable machine is left unset so it re-probes if the hardware changes.
      // Skip if the tab was ever backgrounded: the low FPS is then a throttling
      // artifact, not a GPU signal.
      if (trustworthy && !hidden() && fps < FPS_THRESHOLD) setReduceEffects(true);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVis);
      }
    };
  }, [setReduceEffects]);
}
