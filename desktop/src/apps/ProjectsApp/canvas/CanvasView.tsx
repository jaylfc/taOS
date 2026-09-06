import { CanvasBoard } from "./CanvasBoard";
import { AppErrorBoundary } from "@/components/AppErrorBoundary";

export function CanvasView({
  projectId, projectSlug, elementId,
}: { projectId: string; projectSlug: string; elementId?: string | null }) {
  return (
    <div style={{ height: "100%", padding: 0 }}>
      {/* Contain any canvas/tldraw render crash to a fallback instead of taking
          down the whole Projects app. Keyed by project so switching projects
          gives a fresh boundary. */}
      <AppErrorBoundary key={projectId}>
        <CanvasBoard projectId={projectId} projectSlug={projectSlug} elementId={elementId} />
      </AppErrorBoundary>
    </div>
  );
}
