import { useMemo } from "react";
import { Excalidraw, convertToExcalidrawElements } from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";
import { CanvasElement } from "./canvas-api";
import { elementsToSkeletons } from "./element-to-excalidraw";

// Read-only Excalidraw view over the canonical CanvasElement scene. Slice 2 of
// the tldraw -> Excalidraw migration: the board is built alongside the existing
// tldraw board and not yet wired into CanvasView. Write-back interactions,
// diagram rendering, and the swap that retires tldraw are later slices.

export interface ExcalidrawBoardProps {
  elements: CanvasElement[];
  theme?: "light" | "dark";
}

export function ExcalidrawBoard({ elements, theme = "light" }: ExcalidrawBoardProps) {
  const sceneElements = useMemo(
    () =>
      convertToExcalidrawElements(
        elementsToSkeletons(elements) as unknown as Parameters<
          typeof convertToExcalidrawElements
        >[0],
      ),
    [elements],
  );

  return (
    <div style={{ position: "absolute", inset: 0 }} data-testid="excalidraw-board">
      <Excalidraw
        theme={theme}
        viewModeEnabled
        zenModeEnabled
        initialData={{
          elements: sceneElements,
          appState: {
            viewBackgroundColor: theme === "dark" ? "#0b0f17" : "#ffffff",
          },
          scrollToContent: true,
        }}
      />
    </div>
  );
}

export default ExcalidrawBoard;
