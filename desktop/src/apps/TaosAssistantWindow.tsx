import { useCallback } from "react";
import { useProcessStore } from "@/stores/process-store";
import { TaosAssistantPanelInner } from "@/components/TaosAssistantPanel";

export function TaosAssistantWindow({ windowId }: { windowId: string }) {
  const closeWindow = useProcessStore((s) => s.closeWindow);
  const removeWindow = useProcessStore((s) => s.removeWindow);
  const snap = useProcessStore((s) => s.snapWindow);

  const handleClose = useCallback(() => {
    closeWindow(windowId);
    setTimeout(() => removeWindow(windowId), 250);
  }, [closeWindow, removeWindow, windowId]);

  // This window renders the chat unconditionally: it is reached both via the
  // docked panel's "Pop out" button (which passes props.popOut) and directly
  // from the Launchpad/dock ("taos-agent" is a regular app-registry entry).
  // Gating render on props.popOut left the direct-launch path with nothing
  // to show -- an empty window with no error, no content (#1615).
  return (
    <div className="h-full w-full flex flex-col bg-shell-bg-deep">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-white/5 shrink-0 select-none">
        <span className="text-xs text-shell-text-secondary font-medium tracking-wide uppercase">
          taOS agent
        </span>
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => snap(windowId, null)}
            className="px-1.5 py-0.5 rounded text-[10px] text-shell-text-tertiary hover:bg-shell-surface-hover"
            title="Restore size"
          >□</button>
          <button
            onClick={handleClose}
            className="px-1.5 py-0.5 rounded text-[10px] text-shell-text-tertiary hover:bg-shell-surface-hover"
            title="Close"
          >✕</button>
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">
        <TaosAssistantPanelInner embedded />
      </div>
    </div>
  );
}
