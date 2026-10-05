// dev-only; deleted by the flip+removal card.

/**
 * Which drawing engine the canvas board uses: tldraw (legacy) or excalidraw
 * (interactive replacement).
 *
 * Precedence:
 *   1. URL query ?canvas=<engine> ("tldraw" | "excalidraw")
 *   2. localStorage "taos.canvas.engine" (try/catch: the desktop may run in
 *      contexts where localStorage is unavailable)
 *   3. DEFAULT_ENGINE = "tldraw"
 *
 * This is a development convenience only: it lets dev keep the legacy board
 * working while Excalidraw lands. It is never a user-facing option, and it is
 * deleted by the flip+removal card that makes Excalidraw the default and
 * retires tldraw.
 */
export const DEFAULT_ENGINE = "tldraw";

export function getCanvasEngine(): "tldraw" | "excalidraw" {
  if (typeof window === "undefined") {
    return DEFAULT_ENGINE;
  }
  try {
    const engine = new URL(window.location.href).searchParams.get("canvas");
    if (engine === "tldraw" || engine === "excalidraw") {
      return engine;
    }
  } catch {
    // malformed location: ignore
  }
  try {
    const stored = localStorage.getItem("taos.canvas.engine");
    if (stored === "tldraw" || stored === "excalidraw") {
      return stored;
    }
  } catch {
    // localStorage unavailable: ignore
  }
  return DEFAULT_ENGINE;
}
