import { useEffect, useRef } from "react";

export interface PillTab {
  id: string;
  label: string;
}

interface Props {
  tabs: ReadonlyArray<PillTab>;
  active: string;
  onSelect: (id: string) => void;
}

/**
 * Horizontal-scrolling pill tab strip for the project workspace on mobile.
 * Distinct from the global PillBar (which is the iOS home-indicator widget).
 */
export function WorkspaceTabPills({ tabs, active, onSelect }: Props) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef<HTMLButtonElement>(null);

  // Keep the active pill in view when it changes (e.g. via URL state).
  useEffect(() => {
    activeRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
  }, [active]);

  return (
    <div
      role="tablist"
      data-testid="workspace-tab-pills-scroller"
      ref={scrollerRef}
      className="flex gap-2 overflow-x-auto px-3 py-2 border-b border-white/10
                 [scrollbar-width:none] [-ms-overflow-style:none]
                 [&::-webkit-scrollbar]:hidden"
      style={{
        WebkitOverflowScrolling: "touch",
      }}
    >
      {tabs.map((t) => {
        const isActive = t.id === active;
        return (
          <button
            key={t.id}
            ref={isActive ? activeRef : undefined}
            role="tab"
            aria-selected={isActive}
            onClick={() => onSelect(t.id)}
            className={
              "shrink-0 rounded-full px-3 py-1.5 text-sm transition-colors " +
              (isActive
                ? "bg-white text-black"
                : "bg-white/10 text-white/80 hover:bg-white/20")
            }
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
