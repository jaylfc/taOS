// dev-only; deleted by the flip+removal card
export type CanvasEngine = "tldraw" | "excalidraw";
export const DEFAULT_ENGINE: CanvasEngine = "tldraw";

export function getCanvasEngine(): CanvasEngine {
  // URL query ?canvas=<engine>
  try {
    const params = new URLSearchParams(window.location.search);
    const engine = params.get("canvas");
    if (engine === "tldraw" || engine === "excalidraw") {
      return engine as CanvasEngine;
    }
  } catch (_) {
    // ignore URL parsing errors
  }

  // localStorage "taos.canvas.engine"
  try {
    const engine = window.localStorage.getItem("taos.canvas.engine");
    if (engine === "tldraw" || engine === "excalidraw") {
      return engine as CanvasEngine;
    }
  } catch (_) {
    // ignore localStorage errors
  }

  return DEFAULT_ENGINE;
}
