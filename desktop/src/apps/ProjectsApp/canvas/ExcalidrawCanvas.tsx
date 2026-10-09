import { useCanvasElements } from "./use-canvas-elements";
import { ExcalidrawBoard } from "./ExcalidrawBoard";

export function ExcalidrawCanvas({ projectId, elementId }: { projectId: string; elementId?: string | null }) {
  const elements = useCanvasElements(projectId, elementId);
  return <ExcalidrawBoard elements={elements} />;
}

export default ExcalidrawCanvas;
