import { CanvasBoard } from "./CanvasBoard";

export function CanvasView({
  projectId, projectSlug,
}: { projectId: string; projectSlug: string }) {
  return (
    <div style={{ height: "calc(100vh - 100px)", padding: 0 }}>
      <CanvasBoard projectId={projectId} projectSlug={projectSlug} />
    </div>
  );
}
