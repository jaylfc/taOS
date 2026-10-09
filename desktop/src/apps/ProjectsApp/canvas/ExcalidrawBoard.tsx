import "./excalidraw-assets";
import { Excalidraw, convertToExcalidrawElements } from "@excalidraw/excalidraw";
import type { ExcalidrawSkeleton } from "./element-to-excalidraw";
import type { CanvasElement } from "./canvas-api";
import type { ComponentProps } from "react";
import { useMemo, useState, useEffect, useRef } from "react";
import "@excalidraw/excalidraw/index.css";
import { elementToSkeleton } from "./element-to-excalidraw";
import { mermaidToExcalidraw, type ExcalidrawElements } from "./mermaid-to-elements";

type ExcalidrawElement = ReturnType<typeof convertToExcalidrawElements>[0];

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
     // Batched conversion: build a list of skeletons for all live non-diagram elements
     // and for diagram elements that are still converting (placeholder skeleton).
     // Ready diagrams (diagrams[el.id] non-empty) are spliced in after conversion.
     // This ensures arrow bindings resolve because all skeletons are converted in
     // a single call, allowing Excalidraw to match start/end ids within the same batch.
     const liveEls = live(elements);
     const skeletonList: ExcalidrawSkeleton[] = [];
     const itemList: { kind: 'skeleton' | 'diagram'; element: CanvasElement; skeletonIndex?: number }[] = [];

      for (const el of liveEls) {
        const diagram = diagrams[el.id];
        if (DIAGRAM_KINDS.has(el.kind) && diagram && diagram.length > 0) {
          // Ready diagram: will be spliced in after conversion.
          itemList.push({ kind: 'diagram', element: el });
        } else {
          // Either a non-diagram or a diagram still converting: use its skeleton.
          const skel = elementToSkeleton(el, { rows: elements });
          skeletonList.push(skel);
          itemList.push({ kind: 'skeleton', element: el, skeletonIndex: skeletonList.length - 1 });
        }
      }

     // Convert all skeletons in one batch.
     const convertedList = convertToExcalidrawElements(skeletonList as unknown as SkeletonInput);

     // Build a map from converted element id to taos_id for elements that have taos_id.
     const convertedIdToTaosId = new Map<string, string>();
     // Group converted elements by their taos_id (set via elementToSkeleton -> taosCustomData).
     // Also handle elements without taos_id but with containerId: attach to the group of the
     // converted element whose id matches that containerId.
     const groupedElements = new Map<string, ExcalidrawElement[]>();
     for (const convertedEl of convertedList) {
       const taosId = convertedEl.customData?.taos_id as string | undefined;
       if (taosId) {
         // Directly grouped by taosId
         if (!groupedElements.has(taosId)) {
           groupedElements.set(taosId, []);
         }
         groupedElements.get(taosId)!.push(convertedEl);
         convertedIdToTaosId.set(convertedEl.id, taosId);
       } else {
         const containerId = (convertedEl as any).containerId;
         if (containerId) {
           const containerTaosId = convertedIdToTaosId.get(containerId);
           if (containerTaosId) {
             if (!groupedElements.has(containerTaosId)) {
               groupedElements.set(containerTaosId, []);
             }
             groupedElements.get(containerTaosId)!.push(convertedEl);
           }
           // If containerTaosId is not found, we drop the element (no taos_id and no valid containerId)
         }
         // If no containerId, we drop the element (no taos_id and no containerId)
       }
     }
     // Rebuild the final array in the original order, replacing skeletons with their
     // converted elements and inserting ready diagram elements.
     const finalElements: ExcalidrawElement[] = [];
      for (const item of itemList) {
        if (item.kind === 'diagram') {
          finalElements.push(...(diagrams[item.element.id] || []));
        } else {
          const taosId = item.element.id;
          const convertedForThis = groupedElements.get(taosId) || [];
          finalElements.push(...convertedForThis);
        }
      }

     return finalElements;
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
