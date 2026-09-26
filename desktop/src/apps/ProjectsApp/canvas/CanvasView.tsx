import { useEffect, useId, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent } from "react";
import { Download } from "lucide-react";
import { CanvasBoard } from "./CanvasBoard";
import { canvasApi } from "./canvas-api";
import { AppErrorBoundary } from "@/components/AppErrorBoundary";

export function CanvasView({
  projectId, projectSlug, elementId,
}: { projectId: string; projectSlug: string; elementId?: string | null }) {
  return (
    <div style={{ position: "relative", height: "100%", padding: 0 }}>
      {/* Contain any canvas/tldraw render crash to a fallback instead of taking
          down the whole Projects app. Keyed by project so switching projects
          gives a fresh boundary. */}
      <AppErrorBoundary key={projectId}>
        <CanvasBoard projectId={projectId} projectSlug={projectSlug} elementId={elementId} />
      </AppErrorBoundary>
      {/* Deliberately a sibling of the engine, not inside it: the recovery
          downloads must survive the drawing-engine swap, the tldraw removal,
          and an engine render crash caught by the boundary above. */}
      <CanvasBackupMenu projectId={projectId} projectSlug={projectSlug} />
    </div>
  );
}

function CanvasBackupMenu({ projectId, projectSlug }: { projectId: string; projectSlug: string }) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  const onKeyDown = (e: ReactKeyboardEvent) => {
    if (e.key === "Escape" && open) {
      e.stopPropagation();
      setOpen(false);
      buttonRef.current?.focus();
    }
  };

  const linkStyle: CSSProperties = {
    display: "block",
    padding: "6px 10px",
    borderRadius: 6,
    color: "inherit",
    textDecoration: "none",
    whiteSpace: "nowrap",
  };

  return (
    <div
      ref={rootRef}
      onKeyDown={onKeyDown}
      style={{ position: "absolute", right: 12, bottom: 60, zIndex: 400, fontSize: 13 }}
    >
      {open && (
        <div
          id={menuId}
          role="group"
          aria-label="Canvas backup downloads"
          style={{
            position: "absolute",
            right: 0,
            bottom: "calc(100% + 6px)",
            padding: 4,
            borderRadius: 8,
            background: "var(--color-shell-surface, #1f2230)",
            color: "var(--color-shell-text, #f2f3f7)",
            border: "1px solid var(--color-shell-border, rgba(255,255,255,0.12))",
            boxShadow: "0 6px 20px rgba(0,0,0,0.35)",
          }}
        >
          <a
            href={canvasApi.snapshotTldrUrl(projectId)}
            download={`${projectSlug}-canvas.tldr`}
            onClick={() => setOpen(false)}
            style={linkStyle}
          >
            Download .tldr (open in tldraw)
          </a>
          <a
            href={canvasApi.elementsJsonUrl(projectId)}
            download={`${projectSlug}-canvas.json`}
            onClick={() => setOpen(false)}
            style={linkStyle}
          >
            Download raw elements (.json)
          </a>
        </div>
      )}
      <button
        ref={buttonRef}
        type="button"
        aria-label="Canvas backup"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        title="Canvas backup"
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          padding: "6px 10px",
          borderRadius: 8,
          cursor: "pointer",
          background: "var(--color-shell-surface, #1f2230)",
          color: "var(--color-shell-text, #f2f3f7)",
          border: "1px solid var(--color-shell-border, rgba(255,255,255,0.12))",
          boxShadow: "0 2px 8px rgba(0,0,0,0.25)",
        }}
      >
        <Download size={14} aria-hidden="true" />
        Backup
      </button>
    </div>
  );
}
