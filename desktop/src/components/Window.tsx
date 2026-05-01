import { useCallback, useRef } from "react";
import { Rnd } from "react-rnd";
import { useProcessStore, type WindowState, type SnapPosition } from "@/stores/process-store";
import { getApp } from "@/registry/app-registry";
import { getSnapBounds } from "@/hooks/use-snap-zones";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { WindowContent } from "./WindowContent";

interface Props {
  win: WindowState;
  onDrag: (x: number, y: number) => void;
  onDragStop: () => SnapPosition;
}

export function Window({ win, onDrag, onDragStop }: Props) {
  const { focusWindow, closeWindow, minimizeWindow, maximizeWindow, updatePosition, updateSize, snapWindow } =
    useProcessStore();
  const app = getApp(win.appId);
  const preSnapRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const isMobile = useIsMobile();

  // Dock is fixed at `bottom-3` (12px gap) with height 64px, plus a
  // little breathing room so the window doesn't visually collide with
  // the dock. Total bottom inset = 12 (gap) + 64 (dock) + 8 (breathing)
  // = 84px. Viewport.dockH represents that total reserved area, not
  // just the dock element's height.
  const viewport = {
    width: window.innerWidth,
    height: window.innerHeight,
    topBarH: 32,
    dockH: 84,
  };

  let displayPos = win.position;
  let displaySize = win.size;

  // On phone-sized viewports every window fills the screen between the
  // top bar and the dock. Stored desktop sizes (e.g. 900x600) overflow
  // an iPhone viewport, leaving the user looking at the empty space
  // outside the window's content.
  if (win.maximized || isMobile) {
    // Window renders INSIDE the Desktop container, which already sits
    // below the top bar. So y=0 here means "flush with the top bar".
    // Height subtracts the top bar (already removed by Desktop's flex-1)
    // and the dock reservation so the window bottom stops above the dock.
    displayPos = { x: 0, y: 0 };
    displaySize = {
      w: viewport.width,
      h: viewport.height - viewport.topBarH - viewport.dockH,
    };
  } else if (win.snapped) {
    const snapBounds = getSnapBounds(win.snapped, viewport);
    if (snapBounds) {
      displayPos = { x: snapBounds.x, y: snapBounds.y };
      displaySize = { w: snapBounds.w, h: snapBounds.h };
    }
  }

  const handleDragStart = useCallback(() => {
    focusWindow(win.id);
    if (win.snapped) {
      preSnapRef.current = { ...win.position, ...win.size };
      snapWindow(win.id, null);
    }
  }, [focusWindow, snapWindow, win.id, win.snapped, win.position, win.size]);

  const handleDrag = useCallback(
    (_e: unknown, d: { x: number; y: number }) => {
      onDrag(d.x, d.y);
    },
    [onDrag],
  );

  const handleDragStop = useCallback(
    (_e: unknown, d: { x: number; y: number }) => {
      const snap = onDragStop();
      if (snap) {
        preSnapRef.current = { x: d.x, y: d.y, w: win.size.w, h: win.size.h };
        snapWindow(win.id, snap);
      } else {
        updatePosition(win.id, d.x, d.y);
      }
    },
    [onDragStop, snapWindow, updatePosition, win.id, win.size],
  );

  const handleResizeStop = useCallback(
    (_e: unknown, _dir: unknown, ref: HTMLElement) => {
      updateSize(win.id, ref.offsetWidth, ref.offsetHeight);
    },
    [updateSize, win.id],
  );

  if (win.minimized) return null;

  const minSize = app?.minSize ?? { w: 300, h: 200 };

  return (
    <Rnd
      position={{ x: displayPos.x, y: displayPos.y }}
      size={{ width: displaySize.w, height: displaySize.h }}
      minWidth={minSize.w}
      minHeight={minSize.h}
      style={{ zIndex: win.zIndex }}
      dragHandleClassName="window-titlebar"
      disableDragging={win.maximized || isMobile}
      enableResizing={!win.maximized && !win.snapped && !isMobile}
      onDragStart={handleDragStart}
      onDrag={handleDrag}
      onDragStop={handleDragStop}
      onResizeStop={handleResizeStop}
      onMouseDown={() => focusWindow(win.id)}
      bounds="parent"
      onContextMenu={(e: React.MouseEvent) => e.stopPropagation()}
    >
      <div
        className={`flex flex-col h-full rounded-[var(--spacing-window-radius)] overflow-hidden border ${
          win.focused
            ? "border-shell-border-strong shadow-[var(--shadow-window)]"
            : "border-shell-border shadow-[var(--shadow-window-unfocused)]"
        }`}
        style={{ backgroundColor: "var(--color-shell-bg)" }}
      >
        {/* Titlebar — macOS-style traffic lights. Icons appear inside each
            button when the user hovers anywhere on the group (matches the
            macOS pattern where hover state is shared across all three). */}
        <div className="window-titlebar flex items-center h-8 px-3 shrink-0 bg-shell-surface select-none cursor-default">
          <div className="flex gap-1.5 items-center group/traffic">
            <button
              className="w-3 h-3 rounded-full bg-traffic-close hover:brightness-110 flex items-center justify-center"
              onClick={(e) => { e.stopPropagation(); closeWindow(win.id); }}
              aria-label="Close window"
              title="Close"
            >
              <svg
                className="opacity-0 group-hover/traffic:opacity-100 transition-opacity duration-100"
                width="7" height="7" viewBox="0 0 7 7"
                style={{ color: "rgba(0,0,0,0.55)" }}
              >
                <path d="M1.5 1.5L5.5 5.5M5.5 1.5L1.5 5.5" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" />
              </svg>
            </button>
            <button
              className="w-3 h-3 rounded-full bg-traffic-minimize hover:brightness-110 flex items-center justify-center"
              onClick={(e) => { e.stopPropagation(); minimizeWindow(win.id); }}
              aria-label="Minimize window"
              title="Minimize"
            >
              <svg
                className="opacity-0 group-hover/traffic:opacity-100 transition-opacity duration-100"
                width="7" height="7" viewBox="0 0 7 7"
                style={{ color: "rgba(0,0,0,0.55)" }}
              >
                <path d="M1 3.5H6" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" />
              </svg>
            </button>
            <button
              className="w-3 h-3 rounded-full bg-traffic-maximize hover:brightness-110 flex items-center justify-center"
              onClick={(e) => { e.stopPropagation(); maximizeWindow(win.id); }}
              aria-label={win.maximized ? "Restore window" : "Maximize window"}
              title={win.maximized ? "Restore" : "Maximize"}
            >
              {win.maximized ? (
                /* Inward arrows = restore */
                <svg
                  className="opacity-0 group-hover/traffic:opacity-100 transition-opacity duration-100"
                  width="7" height="7" viewBox="0 0 7 7"
                  style={{ color: "rgba(0,0,0,0.55)" }}
                >
                  <path d="M4.5 1V2.5H6M6 2.5L4 4.5" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round" fill="none" />
                  <path d="M2.5 6V4.5H1M1 4.5L3 2.5" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round" fill="none" />
                </svg>
              ) : (
                /* Outward arrows = maximize */
                <svg
                  className="opacity-0 group-hover/traffic:opacity-100 transition-opacity duration-100"
                  width="7" height="7" viewBox="0 0 7 7"
                  style={{ color: "rgba(0,0,0,0.55)" }}
                >
                  <path d="M1 3V1H3M1 1L3 3" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round" fill="none" />
                  <path d="M6 4V6H4M6 6L4 4" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round" fill="none" />
                </svg>
              )}
            </button>
          </div>
          <div className="flex-1 text-center text-xs text-shell-text-secondary truncate">
            {app?.name ?? win.appId}
          </div>
          <div className="w-12" />
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto bg-shell-bg-deep">
          <WindowContent appId={win.appId} windowId={win.id} />
        </div>
      </div>
    </Rnd>
  );
}
