import { useEffect, useState, type ReactNode } from "react";
import { ChevronLeft } from "lucide-react";

/**
 * MobileSplitView — iOS 26-style navigation primitive for list/detail apps.
 *
 * On desktop (>= breakpoint), renders both panes side-by-side as a classic
 * master/detail split. On mobile, shows one pane at a time and slides
 * between them with a spring-like easing. Back navigation returns to the
 * list with a chevron + list title, matching the iOS UINavigationController
 * pattern.
 *
 * Apps pass `list` and `detail` as ReactNodes plus `selectedId` to drive
 * which pane is visible. The parent still owns state — we just render.
 */

interface Props {
  list: ReactNode;
  detail: ReactNode | null;
  selectedId: string | null;
  onBack: () => void;
  /** Title shown next to the back chevron on mobile */
  listTitle?: string;
  /** Optional title for the detail view, shown centred in the mobile nav */
  detailTitle?: string;
  /** Optional right-side actions for the mobile detail nav */
  detailActions?: ReactNode;
  /** Optional right-side actions for the mobile list nav */
  listActions?: ReactNode;
  /** Breakpoint in px below which we collapse to single-pane (default 768) */
  breakpoint?: number;
  /** Fixed list width on desktop (default 280) */
  listWidth?: number;
}

export function MobileSplitView({
  list,
  detail,
  selectedId,
  onBack,
  listTitle = "",
  detailTitle = "",
  detailActions,
  listActions,
  breakpoint = 768,
  listWidth = 280,
}: Props) {
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.innerWidth < breakpoint,
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mql = window.matchMedia(`(max-width: ${breakpoint - 1}px)`);
    const update = () => setIsMobile(mql.matches);
    update();
    mql.addEventListener("change", update);
    return () => mql.removeEventListener("change", update);
  }, [breakpoint]);

  // Desktop: traditional side-by-side layout.
  // flex-1 + min-w-0 so the split view fills its flex-container parent —
  // without these, a flex item's default flex-basis:auto makes the split
  // view shrink to the intrinsic (min-content) size of its children,
  // starving the detail pane's toolbar of horizontal space.
  if (!isMobile) {
    return (
      <div className="flex h-full min-h-0 overflow-hidden flex-1 min-w-0">
        <aside
          style={{ width: listWidth }}
          className="shrink-0 border-r border-white/5 overflow-y-auto"
        >
          {list}
        </aside>
        <section className="flex-1 min-w-0 min-h-0 overflow-hidden">
          {detail}
        </section>
      </div>
    );
  }

  // Mobile: single-pane with slide transition
  const showingDetail = selectedId !== null;
  return (
    <div data-testid="mobile-split-view" style={{ position: "relative", height: "100%", width: "100%", overflow: "hidden" }}>
      {/* Slider track — two panes each 100%, translated by view state */}
      <div
        style={{
          display: "flex",
          flexDirection: "row",
          width: "200%",
          height: "100%",
          transform: showingDetail ? "translateX(-50%)" : "translateX(0)",
          transition: "transform 320ms cubic-bezier(0.32, 0.72, 0, 1)",
        }}
      >
        {/* List pane — 50% of track = 100% of viewport */}
        <div style={{ width: "50%", height: "100%", display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden" }}>
          {listActions && (
            <MobileNavBar title={listTitle} rightActions={listActions} />
          )}
          <div style={{ flex: 1, overflowY: "auto" }}>{list}</div>
        </div>

        {/* Detail pane — 50% of track = 100% of viewport */}
        <div style={{ width: "50%", height: "100%", display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden" }}>
          <MobileNavBar
            title={detailTitle}
            leftAction={
              <button
                onClick={onBack}
                className="flex items-center gap-0.5 -ml-1 py-1 pr-2 pl-1 rounded-full active:opacity-60 transition-opacity"
                aria-label={`Back to ${listTitle || "list"}`}
                style={{ color: "rgb(100, 180, 255)" }}
              >
                <ChevronLeft size={20} strokeWidth={2.5} />
                <span className="text-[15px] font-medium truncate max-w-[100px]">
                  {listTitle || "Back"}
                </span>
              </button>
            }
            rightActions={detailActions}
          />
          <div style={{ flex: 1, overflowY: "auto" }}>{detail}</div>
        </div>
      </div>
    </div>
  );
}

/**
 * iOS 26-style mobile nav bar — frosted glass, centred title, optional
 * left/right accessories. Used inside MobileSplitView on mobile only.
 */
function MobileNavBar({
  title,
  leftAction,
  rightActions,
}: {
  title?: string;
  leftAction?: ReactNode;
  rightActions?: ReactNode;
}) {
  const hasTitle = !!title;
  return (
    <div
      className="shrink-0"
      style={{
        display: "flex",
        flexDirection: "column",
        background: "rgba(15, 15, 30, 0.7)",
        backdropFilter: "blur(20px) saturate(180%)",
        WebkitBackdropFilter: "blur(20px) saturate(180%)",
        borderBottom: "1px solid rgba(255,255,255,0.06)",
      }}
    >
      {/* Top row — back button / actions */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          padding: "0 12px",
          height: 44,
        }}
      >
        <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center" }}>{leftAction}</div>
        <div style={{ flex: "0 0 auto", display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 4 }}>
          {rightActions}
        </div>
      </div>
      {/* Second row — title on its own line, can wrap, ellipsises at 2 lines */}
      {hasTitle && (
        <div
          style={{
            padding: "0 16px 10px",
            fontSize: 22,
            fontWeight: 700,
            color: "rgba(255,255,255,0.95)",
            letterSpacing: "-0.3px",
            lineHeight: 1.2,
            wordBreak: "break-word",
            overflow: "hidden",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
          }}
        >
          {title}
        </div>
      )}
    </div>
  );
}
