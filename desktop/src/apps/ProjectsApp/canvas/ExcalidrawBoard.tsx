import { useMemo, useState, useEffect } from "react";
import type { ComponentProps } from "react";
import { Excalidraw, convertToExcalidrawElements } from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";
import { CanvasElement } from "./canvas-api";
import { elementsToSkeletons } from "./element-to-excalidraw";

// Read-only Excalidraw view over the canonical CanvasElement scene. Slice 2 of
// the tldraw -> Excalidraw migration: the board is built alongside the existing
// tldraw board and not yet wired into CanvasView. Write-back interactions,
// diagram rendering, and the swap that retires tldraw are later slices.

type ExcalidrawAPI = Parameters<
  NonNullable<ComponentProps<typeof Excalidraw>["excalidrawAPI"]>
>[0];

export interface ExcalidrawBoardProps {
  elements: CanvasElement[];
  theme?: "light" | "dark";
}

export function ExcalidrawBoard({ elements, theme = "light" }: ExcalidrawBoardProps) {
  const [api, setApi] = useState<ExcalidrawAPI | null>(null);

  const sceneElements = useMemo(
    () =>
      convertToExcalidrawElements(
        elementsToSkeletons(elements) as unknown as Parameters<
          typeof convertToExcalidrawElements
        >[0],
      ),
    [elements],
  );

  // Excalidraw opens at scroll origin, so a scene whose elements sit away from
  // (0,0) renders off-screen. Fit the viewport to the content once the API is
  // ready and whenever the scene changes.
  useEffect(() => {
    if (api && sceneElements.length > 0) {
      api.scrollToContent(sceneElements, { fitToContent: true, animate: false });
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
