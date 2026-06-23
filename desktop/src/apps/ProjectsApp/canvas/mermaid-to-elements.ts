import { parseMermaidToExcalidraw } from "@excalidraw/mermaid-to-excalidraw";
import { convertToExcalidrawElements } from "@excalidraw/excalidraw";

// Convert a mermaid/flowchart source string into positioned Excalidraw elements
// for the canvas board. parseMermaidToExcalidraw yields skeleton elements laid
// out from the diagram's own origin; convertToExcalidrawElements fills them in,
// then we translate the whole diagram to the CanvasElement's (x, y). Returns an
// empty array on invalid mermaid so a bad source degrades to nothing (the board
// keeps the placeholder) rather than throwing.

export type ExcalidrawElements = ReturnType<typeof convertToExcalidrawElements>;

export async function mermaidToExcalidraw(
  source: string,
  offsetX: number,
  offsetY: number,
): Promise<ExcalidrawElements> {
  const src = (source || "").trim();
  if (!src) return [];
  try {
    const { elements } = await parseMermaidToExcalidraw(src);
    const converted = convertToExcalidrawElements(elements);
    // Translate the diagram as a whole; bindings between nodes are relative, so
    // shifting every element by the same offset preserves them.
    return converted.map((el) => ({ ...el, x: el.x + offsetX, y: el.y + offsetY }));
  } catch (err) {
    // Degrading to the placeholder is the intended UX, but a swallowed parse
    // error gives no signal in dev; log it so a malformed source is debuggable.
    console.warn("mermaid conversion failed; showing placeholder", err);
    return [];
  }
}
