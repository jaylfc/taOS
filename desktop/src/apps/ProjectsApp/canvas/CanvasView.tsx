import { CanvasBoard } from "./CanvasBoard";

export function CanvasView({
  projectId, projectSlug,
}: { projectId: string; projectSlug: string }) {
  return (
    <div style={{ height: "100%", padding: 0 }}>
      <CanvasBoard projectId={projectId} projectSlug={projectSlug} />
    </div>
  );
}
