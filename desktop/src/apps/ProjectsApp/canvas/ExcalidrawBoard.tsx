import { useMemo, useState, useEffect, useRef } from "react";
import type { ComponentProps } from "react";
import { Excalidraw, convertToExcalidrawElements } from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";
import { CanvasElement } from "./canvas-api";
import { elementToSkeleton } from "./element-to-excalidraw";
import { mermaidToExcalidraw, type ExcalidrawElements } from "./mermaid-to-elements";

// Read-only Excalidraw view over the canonical CanvasElement scene (tldraw ->
// Excalidraw migration). Most kinds map synchronously; mermaid/flowchart kinds
// render their real diagram via mermaid-to-excalidraw, falling back to the
// placeholder rectangle while the (async) conversion runs or if it fails. The
// board is not yet wired into CanvasView; write-back interactions and the swap
// that retires tldraw are later slices.

const DIAGRAM_KINDS = new Set(["mermaid", "flowchart"]);

type ExcalidrawAPI = Parameters<
  NonNullable<ComponentProps<typeof Excalidraw>["excalidrawAPI"]>
>[0];

type SkeletonInput = Parameters<typeof convertToExcalidrawElements>[0];

export interface ExcalidrawBoardProps {
  elements: CanvasElement[];
  theme?: "light" | "dark";
}

function live(elements: CanvasElement[]): CanvasElement[] {
  return elements
    .filter((el) => el.deleted_at == null)
    .slice()
    .sort((a, b) => (a.z_index || 0) - (b.z_index || 0));
}

export function ExcalidrawBoard({ elements, theme = "light" }: ExcalidrawBoardProps) {
  const [api, setApi] = useState<ExcalidrawAPI | null>(null);
  // Converted diagram elements keyed by the CanvasElement id; absent until the
  // async mermaid conversion resolves.
  const [diagrams, setDiagrams] = useState<Record<string, ExcalidrawElements>>({});

  // Re-run conversion only when a diagram element's id/source/position changes.
  const diagramKey = useMemo(
    () =>
      JSON.stringify(
        live(elements)
          .filter((el) => DIAGRAM_KINDS.has(el.kind))
          .map((el) => [el.id, el.x, el.y, (el.payload || {}).source]),
      ),
    [elements],
  );

  useEffect(() => {
    let cancelled = false;
    const diagramEls = live(elements).filter((el) => DIAGRAM_KINDS.has(el.kind));
    if (diagramEls.length === 0) {
      setDiagrams({});
      return;
    }
    (async () => {
      // Convert diagrams in parallel: each parse is independent, so the total
      // wait is the slowest single diagram, not the sum.
      const converted = await Promise.all(
        diagramEls.map(async (el) => {
          const src = String((el.payload || {}).source ?? "");
          return [el.id, await mermaidToExcalidraw(src, el.x, el.y)] as const;
        }),
      );
      if (!cancelled) setDiagrams(Object.fromEntries(converted));
    })();
    return () => {
      cancelled = true;
    };
    // diagramKey captures the inputs that affect conversion.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [diagramKey]);

  const sceneElements = useMemo(() => {
    // Walk the elements in z_index order (live() sorts them) so cross-kind
    // layering is preserved: a diagram is not forced above a regular element
    // that has a higher z_index. A ready diagram contributes its converted
    // elements; everything else (regular kinds, and a diagram still converting)
    // maps through the base skeleton.
    return live(elements).flatMap((el) => {
      if (DIAGRAM_KINDS.has(el.kind)) {
        const ready = diagrams[el.id];
        if (ready && ready.length > 0) return ready;
      }
      return convertToExcalidrawElements([elementToSkeleton(el)] as unknown as SkeletonInput);
    });
  }, [elements, diagrams]);

  // Excalidraw reads initialData once at mount; push later scenes (async
  // diagrams) through the imperative API. Fit the viewport to the content only
  // on the first non-empty scene -- refitting on every update would yank a
  // user's pan/zoom back once interactions land.
  const hasFit = useRef(false);
  useEffect(() => {
    if (!api) return;
    api.updateScene({ elements: sceneElements });
    if (!hasFit.current && sceneElements.length > 0) {
      api.scrollToContent(sceneElements, { fitToContent: true, animate: false });
      hasFit.current = true;
    }
  }, [api, sceneElements]);

  return (
    <div style={{ position: "absolute", inset: 0 }} data-testid="excalidraw-board">
      <Excalidraw
        excalidrawAPI={setApi}
        theme={theme}
        viewModeEnabled
        zenModeEnabled
        initialData={{
          elements: sceneElements,
          appState: {
            viewBackgroundColor: theme === "dark" ? "#0b0f17" : "#ffffff",
          },
        }}
      />
    </div>
  );
}

export default ExcalidrawBoard;
